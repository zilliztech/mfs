# 浏览与读取

这一篇讲 `ls / tree / cat / head / tail / grep` 这六个命令的后台行为：artifact cache 怎么用、大对象怎么处理、密度视图什么时候生效。

## 1. ls 与 tree 的后台行为

```
mfs ls postgres://prod/public/tickets
  │
  ① 查 metadata DB:
       SELECT object_uri, media_type, size_hint, last_seen, fingerprint, extra
       FROM objects
       WHERE connector_id = $cid AND parent_path = '/public/tickets'
       ORDER BY object_uri
  │
  ② 如果 records 的 last_seen 超过 TTL（默认 1h）：
       触发后台 connector.list($path) 刷新（不阻塞当前请求）
       当前请求返回 cached 结果，附 "(may be stale)" 提示
  │
  ③ 渲染输出
```

所有 ls / tree 都走 metadata DB cache，不直接打回 connector——metadata DB 就是虚拟文件系统的 path index。

`--refresh` 强制同步刷新后再列：

```bash
mfs ls --refresh slack://eng/channels
```

`tree` 是递归 ls，同样机制。

## 2. ls 的输出格式

```text
$ mfs ls postgres://prod/public/tickets
TYPE  NAME            MEDIA-TYPE           SIZE        EXTRA
file  schema.json     application/json     2.1 KB
file  rows.jsonl      application/x-ndjson ~1.2 GB     ~12.4M rows (lazy)

$ mfs ls slack://eng/channels/incidents/2026-05-10
TYPE  NAME            MEDIA-TYPE           SIZE        EXTRA
file  messages.jsonl  application/x-ndjson 48 KB       342 messages
file  threads.jsonl   application/x-ndjson 12 KB       18 threads
dir   files/                                            7 attachments

$ mfs ls ./repo
TYPE  NAME            MEDIA-TYPE           SIZE
dir   docs/
dir   src/
file  README.md       text/markdown        4.2 KB
file  pyproject.toml  application/toml     1.1 KB
file  LICENSE         text/plain           11 KB
```

字段：

| 字段 | 含义 |
|---|---|
| TYPE | `file` / `dir` |
| NAME | 节点名（带 media type 后缀） |
| MEDIA-TYPE | MIME |
| SIZE | 字节数或带 `~` 的估算 |
| EXTRA | connector-specific hint（行数、记录数、`lazy` 等） |

`--json` 输出包含完整 `objects` 行：

```json
[
  {
    "type": "file",
    "name": "rows.jsonl",
    "path": "postgres://prod/public/tickets/rows.jsonl",
    "media_type": "application/x-ndjson",
    "size_hint": 1288490188,
    "lazy": true,
    "extra": {"row_count_hint": 12453000},
    "fingerprint": "abc123",
    "last_seen": "2026-05-15T09:21:00Z",
    "indexable": true,
    "search_status": "indexed",
    "capabilities": {
      "cat": "denied_unless_range",
      "grep": "pushdown",
      "tail": false,
      "range": true
    }
  }
]
```

`capabilities` 告诉 agent 这个对象能用哪些命令；`search_status`（`indexed` / `partial` / `building` / `stale` / `not_indexed`）告诉 agent 语义索引就绪没——`indexed` 走 `search`，否则降级 `grep`（详见 [08 §7](08-agent-skill.md#7-让-agent-自己发现能力)）。两个字段一起让 agent 不用试错。

## 3. tree 的无界处理

slack / discord / gmail 这种按日期递归很容易爆炸（365 天 × 100 频道）。规则：

- 默认 `-L 2`，不是 unlimited
- 每层最多 100 项，超过显示 `... (N more, narrow with <path>)`
- 时间分区目录默认时间倒序，只展开最近 30 天
- 用户加 `--limit N` 调整单层上限，`-L N` 调整深度

```text
$ mfs tree slack://eng -L 3
slack://eng
├── channels/
│   ├── general__C01/
│   │   ├── 2026-05-15/  (today)
│   │   ├── 2026-05-14/
│   │   └── ... (28 more days, narrow with <path>)
│   ├── incidents__C02/
│   │   └── ... (similar)
│   └── ... (97 more channels, narrow with <path>)
├── dms/
└── users.jsonl
```

## 4. 分页与大对象

### 4.1 不用 cursor token

cursor 是 stateful 复杂度，agent 难管、token 过期、兼容性问题多。MFS 用更简单的几条命令组合应对：

- 真要遍历大对象用 `mfs export` 物化到本地再处理
- 大对象过滤用 `mfs grep`（server-side pushdown），不需要全量拉回
- 增量数据走"周期 `mfs add <uri>` + `mfs head -n N` 看快照"，v0.4 不做流式跟随
- DB query 结果不稳定靠 `export` 物化解决

### 4.2 统一接口

```bash
mfs head -n N <uri>          # 固定前 N 行/记录，无状态
mfs tail -n N <uri>          # 固定后 N 行/记录，无状态
mfs cat <uri>                # 完整对象；大对象拒绝并提示
mfs cat <uri> --range A:B    # 按行/记录区间读取
mfs export <uri> <file>      # 完整导出到本地
```

职责不重叠：

- `head / tail` 只看端点，不带范围
- `cat` 默认完整；`--range A:B` 取闭开区间

### 4.3 `--range A:B` 单位

| 对象 media_type | `--range A:B` 单位 |
|---|---|
| `text/*` / `application/x-*-source-code` | 行号 |
| `application/x-ndjson` / `text/jsonl` | record 索引 |
| `text/csv` | row 索引（不含 header） |
| `application/pdf` / `application/x-pdf` | 页码 |
| `application/octet-stream` / `image/*` | 不支持，报 `range_unsupported` |

省略一边：`--range 100:` = 从 100 到末尾；`--range :100` = 0 到 100。

### 4.4 大对象拒绝

`cat` 不接受没有 `--range` 的大对象请求。阈值由 server 端 `server.toml` 控制：

```toml
[cat]
max_full_size = "10MiB"
max_full_records = 10000
```

错误模板：

```text
Object is too large for full cat: postgres://prod/public/tickets/rows.jsonl
size_hint: 4.2GiB
try:
  mfs head -n 20 postgres://prod/public/tickets/rows.jsonl
  mfs cat postgres://prod/public/tickets/rows.jsonl --range 0:1000
  mfs export postgres://prod/public/tickets/rows.jsonl ./tickets.jsonl
```

错误码 `object_too_large_for_cat`。

## 5. cat / head / tail 的数据流

```
mfs cat postgres://prod/public/tickets/rows.jsonl --range 0:100
  │
  ├─ 1. 查 metadata DB：该 object 是否有 artifact cache？
  │
  ├─ 2a. 有 artifact && fresh（fingerprint 一致）：
  │       从 object store 读 artifact bytes（按 range 切片）→ 流回 client
  │
  ├─ 2b. 有 artifact && stale：
  │       异步触发 artifact rebuild
  │       本次仍用 stale artifact（附 `(stale)` 提示）
  │
  ├─ 2c. 无 artifact：
  │       connector.read(path, range) → 流回 client
  │       同时写 artifact_cache（如该 object 的 artifact_kind 配置允许）
  │
  └─ 3. 按 media_type 渲染输出
```

artifact cache 不是必须——有些对象（小文件、纯文本）不值得 cache，每次现拉即可。每类 connector 在 `objects` 表里标记每个对象是否要 cache。

## 6. grep 的派发

`mfs grep` 是**关键词 / 全文搜索**：能下推精确匹配的优先下推，否则走已建好的 BM25 索引——这套对 CS / 异构多 connector 是统一的。派发优先级：

```
mfs grep "ERR_TIMEOUT" <path>
  │
  ├─ 1. 解析 path → 拿到 object 元信息 + capabilities
  │
  ├─ 2a. capabilities.grep == "pushdown"：（精确 + 完整 + 便宜）
  │       connector.grep(pattern, path) → 流式 yield matches（重写了下推实现）
  │       例：postgres → SQL ILIKE；slack → search.messages；s3 → S3 Select
  │
  ├─ 2b. 对象已索引（Milvus 里有 chunks）：（默认主路径，统一 / CS 友好）
  │       Milvus sparse_vec BM25 召回 → 返回 chunk 片段（带 locator / lines）
  │       关键词级、非 regex 精确（BM25 是 token 统计相关）
  │
  └─ 2c. 否则（未索引 + 共享 fs 本地 / 小对象）：（便宜时才走的兜底）
        connector.read() 流式线性扫 + 限速
        超过 `max_grep_bytes` 时截断并提示
```

输出按 path/URI 分组（unix grep 风格）；下推 / artifact 线性扫这类能定位到行的路径给行号，BM25 路径给 chunk 片段 + `locator`（body chunk 形如 `{"lines":[s,e]}`，结构化对象是连接器 PK dict）：

```text
$ mfs grep "ERR_TIMEOUT" s3://logs/app/2026-05-10
s3://logs/app/2026-05-10/app.jsonl
8842  {"level":"error","code":"ERR_TIMEOUT","request_id":"r_123"}
9105  {"level":"error","code":"ERR_TIMEOUT","request_id":"r_456"}
```

`-C N` 上下文行数；`-i` 忽略大小写；`-w` 整词；`-E` 扩展正则。

下推与否对用户透明：用户只用 `mfs grep`，框架根据 connector 能力派发。`mfs status --verbose <uri>` 可看到该对象的 grep 实现路径。

为什么默认不"线性扫原始字节"：CS 模式下 server 常没有原始字节（远端 connector 在 API 后、file 字节在 staging），异构 `--all` 也没法对 SQL 表 / Slack / PDF 统一"扫文件"；而 BM25 索引是建 dense 时顺带就有的（同一张 Milvus schema），在 server / 云侧、统一可用。**要保证精确穷尽**：用支持下推的源，或 `mfs export` 出来本地 `grep` / `rg`（MFS 不重造穷尽字节扫，见 [08 §7](08-agent-skill.md#7-让-agent-自己发现能力)）。

## 7. cat 对非文本对象的渲染

cat 按 media_type 决定渲染方式：

| media_type | cat 默认行为 |
|---|---|
| `text/*` | 原文 |
| `application/json` | pretty print（缩进 2 空格） |
| `application/x-ndjson` | 原文（每行一个 JSON） |
| `text/csv` | 表格对齐渲染；`--raw` 出原 CSV |
| `application/pdf` | converted markdown（从 artifact cache 取） |
| `application/vnd.openxmlformats-...` (docx) | converted markdown |
| `image/*` | 提示 `<binary image, 1.2MB>` + artifact 里的 VLM description；`--raw` 输出 bytes |
| 其他 binary | 提示 `<binary, X bytes>`；`--raw` 输出 bytes |

`--raw` 强制原始字节。`--meta` 输出 metadata + 缩略 preview。`--json` 走 envelope。

## 8. 密度视图的适用范围

`--peek / --skim / --deep` 和 W/H/D 参数只对 document / code / directory 形态生效：

| 命令 | 适用对象 | 行为 |
|---|---|---|
| `cat --peek/--skim/--deep` | document / code | heading / symbol skeleton；段落首句；全文展开 |
| `ls --peek/--skim` | directory | 名称列表；+ 每条 summary |
| `tree --peek` | directory | skeleton 树 |

数据来源：

- `--peek`: metadata DB（无需 Milvus）
- `--skim`: Milvus 查该 path 下的 `directory_summary` / `summary` / `vlm_description` chunk，没有则降级到 `--peek`
- `--deep`: Milvus + 取 artifact cache head

对结构化对象（rows.jsonl / messages.jsonl / records.jsonl / schema.json / sample / page_cache）传 `--peek / --skim / --deep` 直接报错：

```text
density view not supported for application/x-ndjson
use head/tail/cat --range instead:
  mfs head -n 20 postgres://prod/public/tickets/rows.jsonl
  mfs cat postgres://prod/public/tickets/rows.jsonl --range 0:50
```

错误码 `density_unsupported`。head/tail/range 已经完整覆盖结构化对象的预览需求，密度视图重复造轮子且语义模糊。W/H/D 参数同样规则。

## 9. v0.4 不支持流式跟随（`tail -f`）

不在 v0.4 范围。每个 connector 要单独搭 push/poll 通道（slack events / discord WS / fs watcher / s3 list polling / DB CDC ...）工程成本太高，受益场景又窄。

替代做法：

```bash
# 周期同步 + 看快照
mfs add slack://eng                              # 触发增量同步
mfs head -n 50 slack://eng/.../messages.jsonl    # 看最新一批

# 用 cron / watch 命令自己包一层
watch -n 30 'mfs add slack://eng && mfs head -n 50 slack://eng/...'
```

视用户呼声决定是否 v0.5+ 引入，届时优先支持 file connector 和 slack / discord 这种自带 push 的源。

## 10. Artifact cache 层细节

> 名词约定：MFS 内部有两层 cache，**职责完全独立**——这里讲的 **artifact cache** 是按 object_uri 寻址的派生产物缓存（converted markdown / VLM 描述 / page cache 等），给 `cat / head / chunker` 用。另一层 **transformation cache** 是按 content_hash 寻址的计算缓存，给 embedder / vlm / summary client 跳过重复 API 调用用，物理上独立 SQLite 文件，详见 [02 §10.4](02-architecture.md#104-cache-层)。

### 10.1 `artifact_cache` 表 schema

```sql
artifact_cache (
  namespace_id     TEXT DEFAULT 'default',   -- 进主键，避免跨 namespace 同名 object_uri 撞车
  object_uri       TEXT,
  artifact_kind    TEXT,            -- "converted_md" | "page_cache" | "head_cache" | "vlm_text" | "schema_dump"
  storage_path     TEXT,            -- ~/.mfs/cache/artifacts/<namespace_id>/<sha1(object_uri)>/<artifact_kind>
  fingerprint      TEXT,            -- 同上游 fingerprint，用于 stale check
  size_bytes       INTEGER,
  built_at         TIMESTAMP,
  last_accessed    TIMESTAMP,        -- LRU 淘汰依据（§10.4）
  PRIMARY KEY (namespace_id, object_uri, artifact_kind)
)
```

### 10.2 几种 artifact 类型

| artifact_kind | 来源 | 谁用 |
|---|---|---|
| `converted_md` | PDF / DOCX / gdoc / HTML 转 markdown | `cat` 直接出 / chunker 输入 |
| `page_cache` | DB rows / Slack messages / S3 list | `cat / head / tail / grep` |
| `head_cache` | head N 的预拉取（如 DB 表前 100 行） | `head` 命中快路径；不暴露给用户 |
| `vlm_text` | 图片的 VLM description | `cat --meta` / `cat --skim` |
| `schema_dump` | DB schema / Mongo sample-inferred schema | `cat schema.json` |

### 10.3 何时建 artifact、何时不建

**核心原则：大集合（`rows.jsonl` / `messages.jsonl` 等虚拟集合对象）不全量物化。** 它们在外部数据源里不真的以文件形态存在——是 MFS 为了让用户能 `cat / head / grep` 而呈现的虚拟接口。MFS 不会把 12M 行的 postgres 表全量 dump 成本地 jsonl（也没人真的会去 cat 12M 行），保持 lazy + 选择性 head_cache 就够。

每个 connector plugin 自己决定细节，一般规则：

- 真实文件（本地文件、GitHub blob、S3 object）→ 不建 artifact，每次 connector.read() 直接拉（要么 fast，要么需要凭据隔离）
- 小元数据（schema.json、users.jsonl 这种几 KB）→ 建 artifact，访问频繁
- 大集合（rows.jsonl / messages.jsonl）→ **不全量物化**。`cat --range A:B` 直接走 connector pushdown（如 SQL `OFFSET LIMIT`）；可选 head_cache 缓存前 N 条加速 `mfs head`
- 图片 VLM → 建 description 文本 artifact，不存图片本身
- PDF / DOCX / HTML 转 markdown → 建 markdown artifact（converter 贵），原文件还在 source

### 10.4 artifact_cache 淘汰

server 端 `server.toml`：

```toml
[artifact_cache]
max_size_gb = 10
eviction = "lru"
```

超出时按 LRU 淘汰；fingerprint 变化时立即失效。

## 11. Pipe 与 stdin

Pipe 是普通 unix 字节流——MFS 不在 stdin/stdout 上发明私有协议，不识别"上游来自哪个 connector"。每个新 connector 不需要做 pipe 元数据适配。

规则：

- 上游 `mfs cat / head / tail / grep / search` 输出纯字节流（默认）或 JSON（`--json`），没有 MFS header
- `mfs search` / `mfs grep` 读 stdin 时总是把 stdin 当临时文本处理
- 想限定到具体 connector 或对象就传 path 参数：`mfs search "..." <path>`
- 无 path、无 `--all`、无 stdin → 报错

示例：

```bash
# 临时搜索 stdin 文本
git log --oneline | mfs search "fix auth"

# 大对象切片后 pipe 到 jq
mfs cat postgres://prod/public/tickets/rows.jsonl --range 0:100 --json | jq '...'

# 限定 connector / 对象，用 path 参数（不要用 pipe 传 source 信息）
mfs search "token expiry" ./docs/auth.md
```

## 12. 端到端示例

### 场景：在 Postgres 大表里找特定 ticket

```bash
# 1. 先看表结构
mfs cat postgres://prod/public/tickets/schema.json

# 2. 看看数据长什么样
mfs head -n 5 postgres://prod/public/tickets/rows.jsonl

# 3. 语义搜索
mfs search "customer cannot login after SSO" postgres://prod/public/tickets --top-k 5
# → 返回 id=12 / id=41 等候选

# 4. 精确读单条（用 search 结果里的 locator）
mfs cat postgres://prod/public/tickets/rows.jsonl --locator '{"pk":{"id":12}}'

# 5. 想离线分析全部 high priority
mfs export postgres://prod/public/tickets/rows.jsonl ./tickets.jsonl
jq 'select(.priority == "high")' ./tickets.jsonl | wc -l
```

### 场景：周期跟随今天的 incidents 频道

```bash
mfs tree slack://eng/channels -L 1                # 看有哪些频道
mfs ls slack://eng/channels/incidents__C02         # 看分了哪些日期

# 周期同步 + 看最新（v0.4 不内置 tail -f）
watch -n 60 'mfs add slack://eng && mfs head -n 20 slack://eng/channels/incidents__C02/today/messages.jsonl'
```

### 场景：在 ./repo 里找 ERR_TOKEN_EXPIRED 怎么处理

```bash
mfs grep "ERR_TOKEN_EXPIRED" ./repo
# src/auth/token.py
# 167  raise TokenExpiredError("ERR_TOKEN_EXPIRED")

mfs cat ./repo/src/auth/token.py --range 150:180
```
