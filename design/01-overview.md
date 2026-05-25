# MFS 设计总览

MFS 是 **Multi-source File-like Search**：让 agent 用一套 shell-native CLI 搜索、浏览和读取本地文件、代码仓库、云文档、消息、ticket、数据库、SaaS 记录和网页。

- 所有数据通过路径或 URI 寻址
- 所有命令是 POSIX 风格（`ls / cat / grep / head / tail / tree`）
- 不挂载文件系统——"file-like" 指 URI 寻址 + POSIX 命令，不是 FUSE
- 检索是混合检索（语义 + 关键词），Milvus 一张 collection 共用

一句话：

> MFS lets agents search and inspect local files, repos, cloud docs, chats, tickets, databases, SaaS records, and web pages through one shell-native CLI. Shell-first — agents drive it via plain commands; SDKs (Python / TypeScript / Go / Java) are available when you need programmatic access.

## 四个核心抽象

```
Connector ─ 一个注册的数据连接器       postgres://prod / ./repo / slack://eng
Object    ─ connector 暴露的一条虚拟文件（path + media_type）
Cache     ─ object 相关的缓存（对外一个概念，自动省重复拉取/计算/花钱）
Chunk     ─ Milvus 一行：能被 search/grep 召回的最小单元
```

整个系统对外只有这四个概念。每个 connector 决定自己 root 下面暴露哪些 object，每个 object 按需生成 cache（PDF→md / 图片→VLM 描述 等派生产物）和 chunks。

> **Cache** 对外是一个概念，内部物理上分两块、用户感知不到：**artifact cache**（per object，让 cat/head/tail 不打回 connector）+ **transformation cache**（per content，跨对象复用 convert/embed/vlm/summary 的 API 结果）。细分见 [02 §10.4](02-architecture.md#104-cache-层)。

**本地文件也是一种 connector**：scheme 是 `file`，用户写普通 path 即可。`postgres connector` / `slack connector` / `file connector` 在概念上一视同仁——同样的 list / stat / read / fingerprint 契约，同样的 chunk pipeline，同样的搜索能力。

`Connector` 这个词沿用业界 ETL / iPaaS 的用法（Airbyte / Kafka Connect / Snowflake Connector），避免跟 shell `source` 命令的语义混淆。

## 系统全景

### 高层抽象

```
┌──────────────────────── Client（薄 · 近乎零状态）────────────────────────┐
│                                                                          │
│   mfs CLI (Rust binary)      SDK (Py/TS/Go/Java)      Agent Skill         │
│        └──────────────────────────┴─────────────────────┘                │
│                     parse args · 解析 profile · 渲染输出                  │
└────────────────────────────────────┬─────────────────────────────────────┘
                                      │  HTTP /v1（主要是 control plane）
                                      ▼
┌──────────────────────── Server（重 · 所有重活）──────────────────────────┐
│                                                                          │
│   API routes ──► Engine ──► Connectors ──► Object ──► Common Services     │
│   /v1/...        业务编排    file/web/pg/   Processors  embedding · VLM ·  │
│                             slack/...       按           summary ·        │
│                             list·stat·read· object_kind  retrieval        │
│                             fingerprint·sync 加工            │            │
│                                                              ▼            │
│                                          DB 任务队列 + Worker pool        │
│                                                   │                       │
└───────────────────────────────────────────────────┼───────────────────────┘
                ┌───────────────┬──────────────────┬─┴──────────────┐
                ▼               ▼                  ▼                ▼
        ┌─────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────────┐
        │ Metadata DB │ │ Object store │ │   Milvus     │ │ Transformation   │
        │ SQLite / PG │ │ artifact     │ │ 一张         │ │ cache            │
        │ 状态+path   │ │ cache        │ │ collection   │ │ 独立 SQLite      │
        │ index+队列  │ │ fs/S3/R2     │ │ vector+BM25  │ │ content-hash 寻址│
        └─────────────┘ └──────────────┘ └──────────────┘ └──────────────────┘
```

CLI 跟 server 走 HTTP、互相不 import。server 按层组织：API → Engine 编排 → Connector 拉数据 → Processor 加工 → Common Service 提供 embedding/VLM/检索 → 四套存储落地。

### 放大：`mfs add` 一次同步里发生了什么

```
mfs add <target>
   │
   ├─① 路径解析     本地 path → file connector
   │                外部 URI  → 对应 connector plugin
   │
   ├─② connector.sync()   流式 yield ObjectChange
   │                       （added / modified / deleted / renamed）
   │
   ├─③ 上游变了就重跑（仅对 yield 出来的 object）
   │       中间贵操作过 cache：convert / embed / vlm / summary
   │       命中复用（零 API）、miss 才花钱；cache key 含 工具+配置+版本
   │       （框架配置变化 = 换 embedding 模型/chunker → v0.4 用户手动
   │         mfs add --force-index；自动检测留 v0.5+）
   │
   ├─④ Worker build task（每个变化对象并行跑）
   │       chunker 按 object_kind 切
   │            │
   │            ▼
   │       cache 命中?
   │            ├─ hit  ──► 复用 vector（零 API 调用）
   │            └─ miss ──► 调 embedding/VLM/summary ──► 异步写回 cache
   │            │
   │            ▼
   │       batch 写 Milvus
   │
   ├─⑤ Deletion reconcile
   │       incremental ──► 跳过（推不出删除）
   │       full scan   ──► 全集 diff，删消失的
   │       explicit "deleted" event ──► 任何模式直接删
   │
   └─⑥ commit connector state + 更新 job 状态
```

每一步背后的细节散在后面各篇：connector 契约见 [04 §5.1](04-connector-and-ingest.md#51-connector-契约两条最小-api)，变化检测 + cache 见 [04 §5.2](04-connector-and-ingest.md#52-重建与-cache)，cache 层见 [02 §10.4](02-architecture.md#104-cache-层)，deletion 见 [02 §7.4](02-architecture.md#74-deletion-策略)。

### 四套存储

职责清晰，互不重叠：

| 存什么 | 存在哪 | 目的 |
|---|---|---|
| Connector / Object / artifact cache / Job 关系 + 状态 | Metadata DB（SQLite 或 Postgres） | path index、状态、变化检测，兼当任务队列 |
| Artifact cache 字节（converted markdown / page cache / VLM description） | Object store（本地 fs / S3 / R2 / MinIO） | 让 `cat / head / tail` 不打回 connector |
| 可检索的 chunk（dense vector + BM25） | Milvus 一张 collection | search / grep 召回 |
| embedding/VLM/summary 计算结果（按 content_hash 寻址） | Transformation cache（独立 SQLite） | 跨对象复用 API 结果，避免重复花钱 |

前三套是面向数据的主存储；第四套 transformation cache 是纯计算缓存（用户感知不到）。具体后端由 server 配置决定，跟 client 端 profile 无关。

## 设计哲学与原则

这一节讲"为什么是这套架构"，是理解后面所有细节文档的底座。

### 1. 客户端薄，重量全压服务端

所有重活——拉数据、转 markdown、切 chunk、调 embedding、写索引、存储——都在 server。client（CLI / SDK）只做四件轻事：解析参数、解析 profile、HTTP transport、渲染输出。

为什么这么分：

- **agent 高频循环调 CLI**，client 必须冷启动快（Rust 单 binary 几十 ms）、零状态。把重逻辑放 client 会让每次调用都背上启动成本和依赖
- **状态集中在 server 才好做一致性**——connector 状态、索引、变化检测都是有状态的事，放一处统一管比分散到每个 client 强
- client **几乎无持久状态**：只有 `client.toml`（profile + client_id）。连本地文件的 manifest 都不在 client，而在 server 的 `file_state` 表——client 切机器 / 重装 / Docker 重建零成本

### 2. 面向 agent：CLI + Skill 为主，SDK 为辅

MFS 第一受众是 **agent**，不是人。所以主接口是 **shell-native CLI**——用 agent 已经会的 POSIX 动词（`ls / cat / grep / head / tail / tree`）驱动，不发明新词汇。

配套发一个 **Skill 包**（`SKILL.md` + 每个 connector 的 PROMPT），让 agent 一上来就有正确的心智模型和各 connector 的目录布局，不用试错。

**SDK（Python / TS / Go / Java）是辅助**：给那些已经是程序、不方便 shell-out 调 CLI 的集成方用。CLI / SDK / Skill 三者走的是**同一套 HTTP `/v1`**，没有谁有特权路径——这保证三种入口行为一致，也意味着加一种 SDK 不影响其他。

### 3. 一切皆 connector，但 file 是唯一特例

统一抽象：postgres / slack / github / file 在概念上一视同仁，都实现同一套 `list / stat / read / fingerprint / sync` 契约，走同一条 chunk pipeline，得到同样的搜索能力。

但 **file connector 是唯一的特例**，原因是数据**位置**：

- 大多数 connector 的上游 server 够得着（server 能连 postgres、能调 slack API）→ server 直接拉
- 只有 file，在 CS 架构下数据在 **client 那台机器上**，server 够不着 → 必须把字节**上传**过来

所以 file connector 比别人多一层"上传协议"（manifest diff → zip 上传 → commit）。这层特殊性被**隔离**得很干净：file connector 的 sync 代码在本地 / CS 两种模式下完全共用，差别只在"字节怎么到达 server 的 scope"——本地直接读盘，CS 经过上传落到 staging area。详见 [02 §4.2](02-architecture.md#42-本地文件-upload-flow不共享-fs-时)。

### 4. 三套存储 + 一层计算缓存，职责正交

为什么不是一个大库装下一切——因为四类数据的**访问模式、持久化要求、扩展特性**完全不同，硬塞一起会互相拖累：

| 存储 | 装什么 | 为什么选它 |
|---|---|---|
| **Metadata DB**（SQLite / Postgres） | connector / object / job 状态、path index、fingerprint、`file_state` | 需要事务 + 索引查询；还顺便**当任务队列**用（`SELECT ... FOR UPDATE SKIP LOCKED`），省掉 Redis/Celery 这种额外组件 |
| **Object store**（fs / S3 / R2） | artifact cache 大字节（converted md / VLM 文本 / page cache）| 大 blob 该放便宜的对象存储，按 object_uri 寻址，给 cat/head 流式读 |
| **Milvus**（一张 collection） | 可检索的 chunk（dense vector + BM25 sparse）| 向量检索需要专门的 ANN 索引，这是 Milvus 的本职 |
| **Transformation cache**（独立 SQLite） | embedding/VLM/summary 调用结果，按 content_hash 寻址 | best-effort、高写入churn、丢了不影响正确性 → 单独文件隔离，不拖累 metadata DB 的事务关键路径 |

核心原则：**source of truth 永远是上游**。Metadata DB 只是"我对上游的认知"，object store 和 Milvus 是从 fingerprint 派生出来的产物。派生层坏了的托底是 `mfs remove + mfs add` 重建。

### 5. 怎么避免重复花钱：两道独立的防线

两类成本，两个机制，各打各的：

- **省带宽**——`file_state` per-path manifest：CS 模式下只上传变化的字节；rename 靠 inode 配对识别，mv 1GB 文件零字节上传
- **省 API 钱**——transformation cache：内容相同 + 模型相同就复用 embedding/VLM/summary 结果，跨 object / 跨 connector / 跨 namespace 都命中，连 Milvus collection 重建、embedding model 回滚都还在

这两道防线让一个看似昂贵的操作（重传、重 embed、误删后恢复）变得廉价——这也是为什么 deletion 策略敢做得简单（详见 §6）。

### 6. 变化检测一层 + cache 兜成本

变化检测就**一层**：上游变没变，**connector 最懂自己的源**自己探测（file 用 stat-first lazy hashing：先看 size+mtime，变了才算 sha1；DB 用 `updated_at` cursor；web 用 ETag……），通过 `ObjectChange` 流式上报 added / modified / deleted / renamed。connector 只 yield 变化的部分，没 yield 的就跳过。

上游变了就**重跑这个 object 的 pipeline**，中间贵操作（convert / embed / vlm / summary）过 **content-addressable cache** 兜成本——cache key 含 `工具 + 配置 + 版本`，所以"换 chunker / 换模型要不要重算"由 key 自然回答，不需要框架维护多层 fingerprint 失效（详见 [04 §5.2](04-connector-and-ingest.md#52-重建与-cache)）。

**框架配置变化**（换 embedding 模型 / 升级 chunker / 改 text_fields）connector 看上游没变、啥都不 yield，**v0.4 不自动检测**，用户手动 `mfs add --force-index` 重建（重跑时 cache 大量命中、只为真变部分花钱）；自动检测漂移留 v0.5+。

删除是其中最微妙的一块：增量同步**推不出删除**（"没 yield" 只代表"没变"，不代表"删了"），只有全量扫描的 diff 或上游明确的 delete 信号才能确定删除。详见 [02 §7.4](02-architecture.md#74-deletion-策略)。

### 7. 一切操作幂等，恢复模型因此极简

MFS 把"幂等"当成贯穿全局的硬约束，每一个操作都能安全地重复执行：

- `mfs add` 再跑 = 再同步一次（注册 + 同步同一个入口，幂等）
- `chunk_id = sha1(namespace + connector + object_uri + chunk_kind + locator + lines)` 内容寻址（`locator`/`lines` 区分同一 object 内的多个 chunk）——写 chunk = DELETE + INSERT，任何 worker / 重试 / 并发对同一 chunk_id 的写都等效
- `mfs remove` 幂等（目标状态就是"消失"，重复 remove 仍然成功）
- per-object 原子 + state 末尾提交：中途崩溃 → state 不 commit → 下次从上个成功点重跑

正因为处处幂等，**整个故障恢复模型坍缩成一句话："重跑 = 下次 `mfs add`"**——没有 `job retry` 命令、没有断点续传的复杂状态机、没有"我跑到哪了"的细粒度记录。崩了就重来，结果一致。

这条还是前面好几个设计敢做简单的底气：deletion 敢激进（重删重建不损坏）、transformation cache 敢做 best-effort（丢了重算就行）、抖动敢直接 abort（下次重跑）——全都建立在"重复执行无害"之上。

### 8. 抽象分层 + 框架/贡献者分工：为社区贡献而设计

整个架构最核心的张力是：**统一公共部分，隔离差异部分**。差异部分越薄，贡献一个新数据源越容易。

从上到下的抽象层：

```
CLI 动词 (ls/cat/grep/...)
  → HTTP /v1                    ← 唯一的 client/server 边界
    → Engine（业务编排）
      → Connector plugin        ← 唯一暴露给贡献者的接缝
        → Object Processor（按 object_kind 加工）
          → Common Service（embedding / VLM / summary / retrieval）
            → Storage（三套后端）
```

**framework 包办（贡献者碰不到）**：chunk 切分、embedding、summary、VLM、Milvus schema、检索、HTTP API、任务队列、变化检测、cache 层、deletion 逻辑。

**贡献者只写一个 connector plugin**（`connectors/<name>/`，约 500–1500 行 Python）：连上游 + 认证、决定虚拟 URI 布局、实现 `stat / list / read`、变化检测（`fingerprint / sync`）、`object_kind` 映射；可选重写 grep / search 下推。

契约就是 **6 个必须实现的方法 + 几个可选重写**。这条分割线是刻意画的——贡献者写完这 500 行，就免费拿到整套 chunk / embed / 检索 / 缓存 / 存储机制。

**这是个为社区设计的架构**：加一个新数据源 = 写一个 connector plugin，不碰框架。目标是靠社区把 connector catalog 做大，就像 Airbyte / Singer 的 connector 生态那样。详见 [07](07-contributing-connector.md)。

### 9. 渐进可用：核心先就绪，agent 不空等

`mfs add` 是后台异步的——丢个 job 立刻返回，不阻塞 agent。在这之上，MFS 让"可用性"**渐进释放**，而不是"全量索引完才能用"：

- **优先级排序让核心先可用**：file connector 按启发式给 task 排序（README / 配置 / `src/` 先，`tests/` / `build/` 后，详见 [02 §6.3](02-architecture.md#63-优先级)）。`mfs add .` 跑到 ~30% 时核心文件已索引完，agent 立刻能搜到关键内容
- **索引状态对 agent 可见**：`mfs status <uri>` 列出每个 object 建好没（chunks 数、fresh / building / not_indexed + 原因），connector 级给 search 可用性（available / partial / building / unavailable，详见 [06 §14](06-search-and-retrieval.md#14-搜索可用性-search-availability)）。agent 据此知道哪些能 search、哪些还在建
- **没建好也不空等**：search 不是唯一路径——未索引对象的 `grep` 不依赖语义索引就绪（走 connector pushdown 或线性扫，详见 [05 §6](05-browse-and-read.md#6-grep-的派发)；已索引对象的 grep 则走 BM25），索引还没就绪时 agent 可降级到 grep 兜底，建好后再用语义 search

合起来：agent "刚 add 完就能开始干活"，能力随索引推进逐步增强，而不是卡在全有/全无的开关上。这条跟 [#7 幂等](#7-一切操作幂等恢复模型因此极简) 互补——幂等保证"重跑安全"，渐进可用保证"中途就有用"。

## Client / Server 与 profile

CLI、SDK 是 client，所有重活在 server。server 有两种部署位置：

| 部署 | 用途 |
|---|---|
| 本机 server（`mfs serve`） | 个人本机，CLI 和 server 共享文件系统 |
| 远端 server | 团队 / 云端，CLI 通过 HTTPS 访问 |

CLI 跟 server 是否共享 fs 由 machine-id 自动比对决定，用户不需要手动配。一致就走"server 直接读本机"，不一致就走 upload flow（详见 [02 §4.2](02-architecture.md#42-本地文件-upload-flow不共享-fs-时)）。Docker / WSL2 / SSH forward 都会自动判 remote。

是否共享 fs 跟存储后端正交——本机 server 也能用 Postgres + Zilliz Cloud + S3，远端 server 也能用 SQLite + 本地 fs。详见 [02 §3](02-architecture.md#3-client-和-server)。

## 最小心智模型

```text
mfs serve start                       启动本机 server 进程
mfs profile use local                 选择本机 server
mfs add .                             注册并同步本地 file connector
mfs add <connector-uri> --config X    首次注册外部 connector
mfs add <connector-uri>               已注册再跑 = 再同步一次

mfs ls / tree <uri>                   浏览结构
mfs cat <uri>                         读对象（大对象拒绝，提示用 head/tail/range）
mfs head -n N / tail -n N             看端点
mfs cat <uri> --range A:B             按行/记录区间
mfs export <uri> <file>               完整导出到本地

mfs search "..." <path>               语义混合搜索
mfs grep "..." <path>                 关键词/全文搜索（connector 可下推）

mfs status [<uri>]                    看 server / connector / freshness / job
mfs connector list/inspect/probe/update/remove
mfs job list/inspect/cancel
```

## 关键设计决策

| 决策 | 详见 |
|---|---|
| `mfs add` 统一注册 + 同步入口，幂等 | [03 §3](03-cli-commands.md#3-mfs-add-是-mfs-connector-add-的高频别名) |
| 对象名带 media type 后缀（`schema.json` / `rows.jsonl` 等） | [09](09-connector-catalog.md) |
| 分页用 `--range A:B`，不需要 cursor token | [05 §4](05-browse-and-read.md#4-分页与大对象) |
| Milvus 一张 collection，partition_key 按 connector_uri | [06 §1](06-search-and-retrieval.md#1-milvus-collection-schema) |
| 检索字段配置：text_fields / metadata_fields / locator_fields + chunk_strategy | [06 §4](06-search-and-retrieval.md#4-字段配置) |
| 变化检测一层（上游变没变）+ cache 兜中间成本（key 含工具+配置+版本） | [04 §5.2](04-connector-and-ingest.md#52-重建与-cache) |
| 两层 cache：artifact cache（per object_uri）+ transformation cache（per content_hash + model），跨对象复用 embedding/VLM/summary 调用 | [02 §10.4](02-architecture.md#104-cache-层) |
| Deletion：incremental 不删 / full scan 全集 diff 推断；抖动靠 retry + 枚举契约挡，不设阈值 | [02 §7.4](02-architecture.md#74-deletion-策略) |
| HTTP 只走 control plane；client 上传只在 remote profile + 本地路径时发生 | [02 §4](02-architecture.md#4-控制面-vs-数据面) |
| 社区贡献新 connector ~500-1500 行 Python，集中在 `connectors/<name>/` | [07](07-contributing-connector.md) |

## 阅读顺序

按"从顶层 → 细节、从架构 → 命令、从用户 → 贡献者"递进：

| # | 文档 | 内容 | 适合谁 |
|---|---|---|---|
| 01 | [01-overview.md](01-overview.md) | 定位、抽象、系统图、**设计哲学与原则**、决策索引 | 所有人 |
| 02 | [02-architecture.md](02-architecture.md) | client/server、profile、存储、队列、一致性、并发、多租户 | 所有人，运维和贡献者重点看 |
| 03 | [03-cli-commands.md](03-cli-commands.md) | 16 个公开命令、行为契约、JSON envelope、错误码 | 用户、agent 集成方 |
| 04 | [04-connector-and-ingest.md](04-connector-and-ingest.md) | connector 注册、ingest 流程、变化检测 + cache、checkpoint | 用户、贡献者 |
| 05 | [05-browse-and-read.md](05-browse-and-read.md) | ls/tree/cat/head/tail/grep 的后台行为、artifact cache、大对象 | 用户、agent skill 作者 |
| 06 | [06-search-and-retrieval.md](06-search-and-retrieval.md) | Milvus schema、chunk_kind、locator、字段配置、preset | 用户（高级配置）、贡献者 |
| 07 | [07-contributing-connector.md](07-contributing-connector.md) | 插件接口、骨架、对象命名规范 | 贡献者 |
| 08 | [08-agent-skill.md](08-agent-skill.md) | 给 LLM agent skill 作者的指南 | Skill 作者、prompt 工程师 |
| 09 | [09-connector-catalog.md](09-connector-catalog.md) | 每类 connector 的虚拟目录布局清单 | 用户、贡献者参考 |
| 10 | [10-packaging-and-deployment.md](10-packaging-and-deployment.md) | 打包、部署形态、发版、工程目录、运维 | 运维、维护者 |

## 与 Mirage 的关系

参考了 [strukto-ai/mirage](https://github.com/strukto-ai/mirage) 的两个核心思路：

- 每个 connector 自定义虚拟目录布局，用 PROMPT 描述给 agent 看
- 文件名带 media type 后缀（`rows.jsonl` / `schema.json`），cat 按后缀渲染

MFS 跟 Mirage 的核心区别：

| 维度 | Mirage | MFS |
|---|---|---|
| 协议 | FUSE mount + VFP | HTTP /v1，不挂载 |
| 检索 | 不做 | 混合检索（向量 + BM25） |
| 索引产物 | 仅 TTL cache | 持久化 artifact cache + Milvus chunks |
| LLM/VLM 增强 | 不做 | summary、VLM description |
| 目标用户 | 想把数据源当文件系统的 agent | 想用 shell 命令做语义搜索的 agent |
