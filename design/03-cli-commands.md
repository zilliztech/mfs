# CLI 命令清单

MFS 对外公开 16 个顶级命令：11 个 POSIX 风格动词 + 5 个名词管理。CLI 是 HTTP client，所有重活通过 `/v1` API 转给本机或远端的 server。

```
mfs
├── 动词命令（POSIX 风格，agent 不学新词）
│   ├── add <uri>            注册 + 同步（幂等，再跑=再同步）
│   ├── status [<uri>]       daemon / connector / freshness / job
│   ├── 浏览       ls · tree
│   ├── 读取       cat · head · tail · export
│   ├── 搜索       search（语义混合）· grep（关键词/全文，可下推）
│   └── remove <uri>         注销 + 清理（destructive）
│
└── 名词管理（子命令子树）
    ├── connector   add · probe · list · inspect · update · remove
    ├── profile     add · use · list · status
    ├── serve       start · stop · status · logs
    ├── job         list · inspect · cancel
    └── config      show · set
```

`mfs add` / `mfs remove` 是 `mfs connector add/remove` 的高频别名（§3）；其余写操作收敛在 `mfs connector` 子树下。

## 1. 完整命令清单

### 动词命令（POSIX 风格）

| 命令 | 作用 |
| --- | --- |
| `mfs add <uri>` | 注册并同步本地路径或外部 connector。幂等：再跑等于"再同步" |
| `mfs status [<uri>]` | 看 daemon / profile / connector / freshness / job |
| `mfs search <query> <path>` | 语义 + 关键词混合搜索 |
| `mfs grep <pattern> <path>` | 关键词/全文搜索，能下推时下推（精确性随路径而变，见 [05 §6](05-browse-and-read.md#6-grep-的派发)）|
| `mfs ls <uri>` | 列子节点 |
| `mfs tree <uri>` | 树状浏览 |
| `mfs cat <uri>` | 读取对象；大对象拒绝并提示 head/tail/range/export |
| `mfs head <uri>` | 前 N 行/记录 |
| `mfs tail <uri>` | 后 N 行/记录（v0.4 不支持 `-f` 流式跟随） |
| `mfs export <uri> <file>` | 把对象写到本地文件 |
| `mfs remove <uri>` | 注销 connector + 删 chunks / artifact cache / state（destructive，默认 confirm） |

### 名词管理命令

| 命令 | 子命令 |
| --- | --- |
| `mfs connector` | `add / probe / list / inspect / update / remove` |
| `mfs profile` | `add / use / list / status` |
| `mfs serve` | `start / stop / restart / status / logs` |
| `mfs job` | `list / inspect / cancel` |
| `mfs config` | `show / set` |

### URI 写法

所有 `<uri>` 参数都是 connector URI。本地路径是 `file` scheme URI 的简写：

| 用户写 | CLI 规范化（用户可见形式） |
|---|---|
| `./repo`（相对路径） | `file:///<resolved-abs-path>/repo` |
| `/abs/path`（绝对路径） | `file:///abs/path` |
| `file:///abs/path` | 不变 |
| `file://./repo` | 报错（违反 URI 规范，相对路径不能跟 `file://` 一起用） |
| `postgres://prod` | 不变（按 scheme 路由到对应 connector） |

> 这列是**用户可见的规范化形式**——CLI 输入、搜索结果的 `source`、`mfs ls` 输出都用它。file connector 的**内部身份**会再焊上 client_id（`file://<client_id>/<abs-path>`，见 [02 §3.4](02-architecture.md#34-client_idclient-的稳定身份)），用于跨 add / search / remove 的去重；但 client_id 是 UUID、对用户无意义，**不出现在用户面**，CLI 也不接受带 client_id 的写法。要看完整内部 URI（如排查孤儿 connector）走 `mfs connector list / inspect`。

## 2. 设计原则

- 动词命令跟 POSIX 同名同义，agent 不学新词
- 管理类操作集中到名词子树：`mfs connector list` 不是 `mfs list-connectors`
- 一个幂等命令搞定就够：`mfs add` 一个动词承担注册和同步两件事
- 隐藏复杂度：单条 issue / row / message 通过 `locator` 表达，不让用户构造伪路径
- 大对象有 guard：`cat` 默认拒绝大对象并给替代建议

## 3. `mfs add` 是 `mfs connector add` 的高频别名

所有"对 connector 的写操作"集中在 `mfs connector` 子树下；`mfs add` 是最高频的 `connector add` 的 alias。其他 connector 操作（probe / list / inspect / update / remove）走 `mfs connector` 子命令。

```bash
# 主入口
mfs connector add <uri> [--config <toml>]      # 注册并同步
mfs connector probe <uri> [--config <toml>]    # 试连接，不写状态
mfs connector list
mfs connector inspect <uri>
mfs connector update <uri> --config <toml>
mfs connector remove <uri>

# 高频简写
mfs add <uri>                                  # alias = mfs connector add <uri>
```

本地路径是 file scheme URI 的简写（`./repo` 等价于 `file:///<resolved-abs-path>`，CLI 自动展开）；外部 connector 首次需要 `--config`。命令幂等：再跑一次 = 再同步一次。

```bash
# 本地路径（file connector）
mfs add .
mfs add ./repo
mfs add ./repo --watch                  # 启动 watcher
mfs add ./repo --force-index            # server 端强制重 chunk + embed（不重传字节）
mfs add ./repo --force-upload           # 全量上传（imply --force-index）— upload flow 专用

# 外部 connector
mfs add postgres://prod --config x.toml             # 首次：注册 + 同步（默认 confirm）
mfs add postgres://prod --config x.toml --yes       # 跳过 confirm
mfs add postgres://prod                             # 已注册：再同步
mfs add postgres://prod --force-index               # 强制重 chunk + embed
mfs add slack://eng --since 2026-05-01              # 时间游标增量

# 想先试连接、不写状态
mfs connector probe postgres://prod --config x.toml
```

核心 flag：

| flag | 作用 |
|---|---|
| `--config <toml>` | 外部 connector 首次注册必填；已注册时忽略（要改配置用 `mfs connector update`） |
| `--yes` | 跳过 confirm。默认行为：首次注册外部 connector 估算成本后等确认；本地小目录直接跑 |
| `--watch` | 仅本地路径有效，启动 daemon 内 watcher |
| `--interval <dur>` | 仅配合 `--watch`，watcher 的扫描/去抖间隔（如 `60s`），不写走默认 |
| `--no-watch` | 仅本地路径有效，停止该路径上 daemon 内已登记的 watcher 并删除其 watch_grant（保留 connector + 索引；否则 daemon 重启会 replay 重新登记） |
| `--force-index` | 跳过 fingerprint 比对，server 端强制重 chunk + embed。**不重传字节**（upload flow 下 manifest diff 仍然有效）。覆盖 95% "我要 force" 的场景。**默认 confirm**：会重新跑 estimate（chunker + 本地 tokenizer，不打 embedding API）展示要重 embed 的 chunks / tokens 量，按 y/N 决定；`--yes` 跳过 |
| `--all` | **范围 flag**，配合 `--force-index` 用：把 force-index 应用到所有已注册 connector（不带 `--all` 时只作用于 URI 指定的那一个）。换全局 embedding 模型后一次重建全部用它。底层 = 对每个 connector enqueue 一个 force_sync job，复用同一套重建逻辑（详见 [02 §6.2](02-architecture.md#62-worker-怎么拉-task)）。**confirm 一次聚合**：把所有 connector 的受影响 chunks / tokens 加总，只弹一次 y/N（`--yes` 跳过），不逐个问。**遇到正有 in-flight sync 的 connector 跳过并报告**：按 [§8.2](02-architecture.md#82-三条规则) sync 中来 force_sync 会被拒，所以这些 connector 留其 sync 跑完、force-index 只作用于其余，命令末尾列出被跳过的让用户重跑 |
| `--force-upload` | 仅 upload flow（remote profile + 本地路径）有效；跳过 manifest diff，所有 path 都按 stale 处理，全量重新上传字节。imply `--force-index`。仅当怀疑 server staging 字节本身坏了时用 |
| `--since <date>` | 仅时间游标 connector（postgres updated_at / slack ts / github / gmail）有效；其他报 `since_unsupported` |
| `--type <kind>` / `--alias <name>` | 脚本场景替代 connector URI 写法：`--type postgres --alias prod` 等价 `postgres://prod`（详见下方「URI 写法」）；跟直接写 URI 二选一 |

> 部分 connector 另有**自己特定的 scope flag**（如 postgres 的 `--tables-only <list>` / `--schema-only`：只索引指定表 / 只拉 schema 不拉行），由各 connector 声明、`mfs connector inspect` 可见。它们不通用，所以不在上面的核心 flag 表里。

不提供 `--force` 短写法——避免歧义（到底重传不重传？）。shared fs 场景下 `--force-upload` 报错 `upload_not_applicable`。

> `mfs add --all` 跟 `mfs search --all` **语义一致**——都表示"对所有 connector"（一个重建、一个搜索）。它们在不同动词上、命令行先打动词，不会混（同 `git add --all` 管文件、`git push --all` 管分支那样，同名按子命令各表其意，是 unix 惯例）。所以不拆成 `--all-connectors` 这种长名。

### 首次注册外部 connector 的默认行为

```text
$ mfs add postgres://prod --config .mfs/connectors/prod-postgres.toml
Connector validated: postgres://prod
Discovered: 38 tables / ~12.4M rows
Estimated (local chunker + tokenizer only — no embedding API calls):
  chunks:    ~14M    (chunker dry-run on a sample of up to 1000 records)
  tokens:    ~2.5B   (apply your provider's per-token rate to estimate $)

Continue? [y/N]
```

只给本地能算清的物理量。**估算阶段不打任何计费 API**——chunker 是确定性算法，tokenizer 是本地库（tiktoken / hf-tokenizers），都免费，用户看到 prompt 时还没花一分钱。**钱不估**（每个 embedding provider 价格不同）。**时间不估**（受 worker 并发 / API rate limit / 网络浮动 10x）。**storage 不估**（≈ chunks × dim × 4byte，跟所选 embedding model 强相关，误差比 chunks/tokens 还大）。实际成本上线后看 `mfs status` 实时进度。

`--yes` 或本地小目录直接开始：

```text
$ mfs add ./repo
Processing 184 files under /repo
Indexed: 184 files scanned, 37 touched, 2 deleted, 412 chunks queued.
Worker running in background. Run `mfs status` to check progress.
```

本地大目录（默认阈值：超过 5000 个 indexable 文件，或抽样外推 chunks > 50k）也进 estimate + confirm 路径，跟外部 connector 一致：

```text
$ mfs add ./huge-monorepo
Scanning ./huge-monorepo ... 84,231 files, 6.2 GB
Estimated (local chunker + tokenizer only — no embedding API calls):
  chunks:   ~412k    (chunker dry-run on sample)
  tokens:   ~89M     (apply your provider's per-token rate to estimate $)

Continue? [y/N]
```

阈值由 server.toml `[estimate] local_confirm_files` / `local_confirm_chunks` 控制；`--yes` 跳过；CI / 脚本里习惯 `--yes` 的用户不受影响。

如果检测到 rename（`mv ./repo/projects/old ./repo/projects/new` 后），输出多一行 `N renames`：

```text
$ mfs add ./repo
Processing 184 files under /repo
Detected: 100 renames, 0 modified, 0 added, 0 deleted
Renames skip re-embedding (content unchanged); only chunk_id is rewritten.
```

rename 检测算法（inode + sha1 fallback）和触发条件详见 [04 §5.7](04-connector-and-ingest.md#57-rename-detection)。

### 换了 framework 配置怎么办（v0.4：手动重建）

换了 embedding model / chunker config / converter 版本——**v0.4 不自动检测**，普通 `mfs add` 只处理 upstream 变化，不会发现这类框架配置变化。用户改了配置自己知道，手动重建：

```bash
mfs add postgres://prod --force-index    # 重建单个 connector
mfs add --all --force-index              # 换全局 embedding 模型时，重建全部
```

`--force-index` 估算受影响 chunks / tokens 后 confirm（`--yes` 跳过），强制重 chunk + 重 embed（embed 走 transformation cache，内容没变的命中复用）。

> 自动检测配置漂移 + 分级提示（`mfs status / add / search` 三处）是 v0.5+，详见 [04 §5.2](04-connector-and-ingest.md#52-重建与-cache)。

### URI 写法

connector URI 是主推风格（跟 DSN / connection string 一致）。脚本场景可以用 `--type / --alias`：

```bash
mfs add postgres://prod --config x.toml
mfs add --type postgres --alias prod --config x.toml      # 等价
```

### Remote profile 下处理本地路径

remote profile（不共享 fs）下 `mfs add ./repo` 自动走 upload flow——CLI 端 scan + manifest diff + zip bundle 上传 + commit。默认 confirm：

```text
$ mfs add ./repo                                     # active profile = remote
Scanning ./repo ... 184 files, 28 MB
Manifest diff against server: 37 changed, 2 deleted, 145 unchanged
Estimated upload: 8.3 MB (changes only)

Continue? [y/N]
```

`--yes` 跳过 confirm；`--no-upload` 显式拒绝上传（报错而不是发数据）。

upload 完成后 server 跑标准 chunk → embed → 写 Milvus 流程。`mfs status ./repo` 看进度。详见 [02 §4.2](02-architecture.md#42-本地文件-upload-flow不共享-fs-时)。

## 4. Search

```bash
mfs search "session storage" ./src --top-k 5
mfs search "customer cannot login" postgres://prod/public/tickets
mfs search "session" --all                # 跨所有已注册 connector
```

输出（本地）：

```text
[1] src/session/store.py  score=0.884
 82  class SessionStore:
 83      def save(self, session: Session) -> None:

[2] src/auth/session.py  score=0.731
 14  SESSION_COOKIE_NAME = "sid"
```

输出（外部 connector）：

```text
[1] postgres://prod/public/tickets/rows.jsonl  score=0.842
     row: id=12
     subject: Login broken after SSO migration
     status: open
     priority: high
```

- 召回走 Milvus hybrid（dense + sparse + RRF）
- 必须显式给 `<path>` 或 `--all`，不会默认搜全部
- 返回结果含可继续操作的 `source` URI 和 `locator`

## 5. Grep

```bash
mfs grep "ERR_TOKEN_EXPIRED" .
mfs grep -C 5 "OAuth" ./docs
mfs grep "SSO" postgres://prod/public/tickets/rows.jsonl
mfs grep "timeout" slack://eng/channels/incidents
```

输出按 path/URI 分组（unix grep 风格）：

```text
src/auth/token.py
167  raise TokenExpiredError("ERR_TOKEN_EXPIRED")

slack://eng/channels/incidents/2026-05-10/messages.jsonl
118  {"ts":"1715320060.456","user":"U2","text":"api timeout is rising"}
```

`mfs grep` 是**关键词 / 全文搜索**——能精确就精确，否则走已经建好的 BM25 索引（CS / 异构多 connector 下统一可用）。按优先级派发（详见 [05 §6](05-browse-and-read.md#6-grep-的派发)）：

- connector 支持下推（`grep_pushdown=true`）→ 下推为 SQL `ILIKE` / Slack search API / S3 Select：**精确 + 完整 + 便宜**
- 否则对象已索引 → Milvus `sparse_vec` BM25：**统一、便宜、CS 友好**；关键词级、非 regex 精确，返回 chunk 片段（带 `locator` / `lines`）
- 否则（未索引 + 共享 fs 本地 / 小对象）→ `connector.read()` 线性扫：仅在便宜时的兜底，超 `max_grep_bytes` 截断

为什么默认不"线性扫原始字节"：CS 模式下 server 手上常没有原始字节（远端 connector 在 API 后、file 字节在 staging），异构 `--all` 也没法对 SQL 表 / Slack / PDF 统一"扫文件"；而 BM25 索引是建 dense 时顺带就有的、在 server/云侧、统一可用。**要保证精确穷尽**：用支持下推的源，或 `mfs export` 出来本地 `grep` / `rg`（MFS 不重造穷尽字节扫，见 [08 §7](08-agent-skill.md#7-让-agent-自己发现能力)）。

## 6. ls / tree / cat

### ls

```bash
mfs ls postgres://prod/public/tickets
```

```text
TYPE  NAME            MEDIA-TYPE           SIZE      EXTRA
file  schema.json     application/json     2.1 KB
file  rows.jsonl      application/x-ndjson ~1.2 GB   ~12.4M rows (lazy)
```

- 数据从 metadata DB 取，stale 时后台 refresh（详见 [05 §1](05-browse-and-read.md#1-ls-与-tree-的后台行为)）
- `--refresh` 强制刷新
- 无界目录（slack 几百频道、s3 海量 key）默认截断 100 项 + 提示

### tree

```bash
mfs tree --peek -L 2 ./docs/
mfs tree slack://eng -L 3
```

- 默认 `-L 2`
- 大目录单层超过 100 截断
- 时间分区目录默认时间倒序，只显示最近 30 天

### cat

```bash
mfs cat ./README.md                                              # 完整读文件
mfs cat ./README.md --range 40:90                                # 行范围
mfs cat postgres://prod/public/tickets/schema.json               # JSON
mfs cat postgres://prod/public/tickets/rows.jsonl --range 0:100  # 区间
mfs cat postgres://prod/public/tickets/rows.jsonl --locator '{"pk":{"id":12}}'  # 按 locator 取单条
mfs cat ./docs/diagram.png --meta                                # 看 VLM description
```

完整 cat 大对象会被拒绝，提示用 head/tail/range/export。详见 [05 §4](05-browse-and-read.md#4-分页与大对象)。

`--locator '<json>'` 用 search/grep 结果里的 `locator` 精确取回**单条完整记录**——可下推的 connector 走精确查询（postgres `WHERE pk=...`），否则 framework 流式扫 records 匹配。比 `grep '"id":12'` 可靠：不受 `text_fields` 覆盖范围和 BM25 近似的限制。给的 locator 找不到对应记录时报 `locator_not_found`。`--locator` 与 `--range` 互斥。

## 7. 密度视图 `--peek / --skim / --deep`

`ls / tree / cat` 支持三档密度，**仅对 document / code / directory 形态生效**：

| 命令 | 用途 | 数据来源 |
|---|---|---|
| `--peek` | 只列名字 / 标题骨架 | metadata DB |
| `--skim` | + 每条 summary 一行 | Milvus 查 `directory_summary` / `summary` / `vlm_description` |
| `--deep` | 展开更多结构 | Milvus + artifact cache head |

```bash
mfs tree --peek -L 2 ./
mfs ls --skim ./docs
mfs cat --skim ./docs/auth.md
```

对结构化对象（rows.jsonl / messages.jsonl / records.jsonl / schema.json）传 `--peek/--skim/--deep` 直接报错：

```text
density view not supported for application/x-ndjson
use head/tail/cat --range instead:
  mfs head -n 20 postgres://prod/public/tickets/rows.jsonl
```

错误码 `density_unsupported`。理由：head/tail 已经覆盖结构化对象的预览需求，密度视图重复造轮子。W/H/D 参数同样规则。

## 8. head / tail / export

```bash
mfs head -n 20 postgres://prod/public/tickets/rows.jsonl
mfs tail -n 50 s3://logs/app/2026-05-10.jsonl
mfs export postgres://prod/public/tickets/rows.jsonl ./tickets.jsonl
```

- `head -n N` / `tail -n N` 无状态
- v0.4 不支持 `-f` 流式跟随——需要每个 connector 单独实现 push/poll 通道，工程成本高、受益场景窄。监控类用例可以脚本化 `mfs add <uri>` 周期同步 + `mfs head -n N` 看快照
- `export` 完整写到本地文件，是大对象遍历的标准做法。**估算 size > `export.warn_size`（server.toml 默认 1 GiB）会先 confirm**（展示 size_hint + 走 connector 是否会触发 API quota），`--yes` 跳过；估算 size > `export.max_size`（默认 100 GiB，0 = 不限）直接拒绝，让用户 `--range` 分批，避免 agent 不假思索拖整张 100 GB 表。错误码 `export_too_large`

## 9. Status

```bash
mfs status                              # 总览
mfs status postgres://prod              # 单 connector 详情
mfs status --verbose postgres://prod    # 含 retrieval index 细节
mfs status --diagnose                   # 自检 profile / connector / storage / search
mfs status --watch                      # 列正在 watch 的目录（仅 local profile）
```

`--watch` 只对 local profile 有意义；remote profile 下执行返回 `watch_unsupported_on_remote`。

样例输出：

```text
$ mfs status
Profile: local (is_local=true, machine-id matched)
Daemon:  running (pid=4112, port=8765, version=0.4.0)
Connectors: 3 active
  ./repo                    last_add=2026-05-14T09:21:00Z   index=fresh
  postgres://prod           last_add=2026-05-14T07:00:00Z   index=stale (3 tables changed)
  slack://eng               last_add=2026-05-13T22:00:00Z   index=fresh
Jobs:    1 running, 0 failed
Search:  available
```

健康检查、watch 状态、诊断都收敛到 `status` 这一个命令。

## 10. Connector / Profile / Serve / Job / Config 管理

### `mfs connector`

```bash
# 注册并同步（高频简写 mfs add）
mfs connector add postgres://prod --config .mfs/connectors/prod-postgres.toml
mfs connector add ./repo

# 试连接，不写状态
mfs connector probe postgres://prod --config .mfs/connectors/prod-postgres.toml

# 看 / 改 / 删
mfs connector list
mfs connector inspect postgres://prod
mfs connector update postgres://prod --config .mfs/connectors/prod-postgres.toml
mfs connector remove postgres://prod
```

### `mfs remove`

`mfs remove <uri>` 等价于 `mfs connector remove <uri>`，注销 connector 并清理一切：

```bash
mfs remove postgres://prod
mfs remove ./repo
mfs remove postgres://prod --yes        # 跳过 confirm
```

默认 confirm：

```text
$ mfs remove postgres://prod
This will permanently delete:
  - 12,453 chunks in Milvus
  - 3.2 GB artifact cache in object store
  - 38 indexed objects
  - 1 running sync job (will be cancelled)

Continue? [y/N]
```

confirm 后流程：取消正在跑的 sync（如有）→ Milvus `DELETE WHERE connector_uri = X`（按 partition_key 路由，只扫该桶）+ 清 artifact cache + 删 metadata → 注销 connector。详见 [02 §8](02-architecture.md#8-并发协调)。

幂等性：

- 重复 `mfs remove` 返回 `already removing, see job <id>`（不重新触发清理）
- 对不存在的 connector 返回 `not registered`（不报错）

### `mfs profile`

```bash
mfs profile add local --url http://127.0.0.1:8765
mfs profile add prod  --url https://mfs.example.com --token-env MFS_TOKEN_PROD
mfs profile use local
mfs profile list
mfs profile status
```

client / server 是否共享 fs 由 CLI 自动判断（machine-id 比对），详见 [02 §3](02-architecture.md#3-client-和-server)。

### `mfs serve`

```bash
mfs serve start
mfs serve stop
mfs serve restart       # 重启本机 server 进程（改了 ~/.mfs/server.toml 后用）
mfs serve status
mfs serve logs
```

`mfs serve` 是 client 端封装，本质是本机 spawn 一个 `mfs-server` 进程。服务端运维直接用 `mfs-server` binary（systemd / docker entrypoint），详见 [02 §5](02-architecture.md#5-server-启动)。

如果只装了 CLI 没装 `mfs-server`：

```text
mfs serve requires mfs-server package.
Install it with:
  uv tool install mfs-server
```

### `mfs job`

```bash
mfs job list [--failed]
mfs job inspect job_01HX...
mfs job cancel job_01HX...
```

`mfs job cancel` 的实际停止有延迟——单个 in-flight `object_task` **不打断**（避免半截 chunks 进 Milvus 的脏状态），先标 `cancelling`、跑完手头 task 再退。CLI 立即返回：

```text
$ mfs job cancel job_01HX...
Cancellation requested. Current in-flight task will finish first.
  in-flight:   tables/public/events/rows.jsonl (started 8m ago)
  pending:     1,243 tasks (will be marked cancelled)
Watch progress: mfs job inspect job_01HX...
```

详见 [02 §8.3](02-architecture.md#83-sync-中的-remove-流程)。

失败时 `mfs add <uri>` 即可（幂等），所以不提供 `job retry`。

### `mfs config`

```bash
mfs config show                          # 当前 profile 的 client + server 合并视图
mfs config show --effective <uri>        # 某个 connector / 对象的最终生效配置（合并 server.toml + connector TOML + [[objects]] 段）
mfs config set <key> <value>             # 写 client.toml（仅 client 端可改的项）
```

`mfs config set` **只改 client 端 `~/.mfs/client.toml`**——典型用途：默认 profile、CLI 输出格式、超时等。可改 key 由 CLI 内嵌的 schema 限定，乱写报 `unknown_config_key`。

Server 端配置（`server.toml`：metadata backend、object store、Milvus URI、embedding provider 等）**不通过 CLI 改**：

- 本机 server：编辑 `~/.mfs/server.toml` → `mfs serve restart`
- 远端 server：编辑 `/etc/mfs/server.toml` → `mfs-server reload`（或重启进程）

`mfs config show` 跨 client/server 拉数据时，server 端只回敏感字段 redacted 过的副本（token / DSN 不出现）。

## 11. `--json` envelope

每个动词命令都支持 `--json`。统一 envelope：

```json
{
  "source": "postgres://prod/public/tickets/rows.jsonl",
  "lines": null,
  "content": "Login broken after SSO migration",
  "score": 0.842,
  "locator": {
    "schema": "public",
    "table": "tickets",
    "pk": {"id": 12}
  },
  "metadata": {
    "kind": "search",
    "chunk_kind": "row_text",
    "connector_type": "postgres",
    "media_type": "application/x-ndjson",
    "fields": {
      "status": "open",
      "priority": "high"
    }
  }
}
```

字段：

| 字段 | 含义 |
|---|---|
| `source` | 包含该结果的 object URI（可继续 `mfs cat`） |
| `lines` | 文本对象时的 line range `[start, end]`，否则 null |
| `content` | 召回 / 读取的文本 |
| `score` | search/grep 才有；ls/cat 为 null |
| `locator` | 容器内单元定位，schema per connector（见 [06 §3](06-search-and-retrieval.md#3-locator-schema-per-connector)） |
| `metadata` | 包含 `kind`、`chunk_kind`、`connector_type`、`media_type`、connector-specific `fields` |

`cat --range A:B --json` 返回 `items` 数组 + `range` 信息：

```json
{
  "source": "postgres://prod/public/tickets/rows.jsonl",
  "media_type": "application/x-ndjson",
  "range": {"start": 0, "end": 2, "total_hint": 12453},
  "items": [
    {"id": 12, "subject": "Login broken after SSO migration"},
    {"id": 41, "subject": "SSO redirect loop"}
  ]
}
```

## 12. 错误输出

人类文本：

```text
Object is too large for full cat: postgres://prod/public/tickets/rows.jsonl
size_hint: 4.2GiB
try:
  mfs head -n 20 postgres://prod/public/tickets/rows.jsonl
  mfs cat postgres://prod/public/tickets/rows.jsonl --range 0:1000
  mfs export postgres://prod/public/tickets/rows.jsonl ./tickets.jsonl
```

JSON：

```json
{
  "error": {
    "code": "object_too_large_for_cat",
    "message": "Object is too large for full cat",
    "source": "postgres://prod/public/tickets/rows.jsonl",
    "size_hint": "4.2GiB",
    "suggestions": [
      "mfs head -n 20 postgres://prod/public/tickets/rows.jsonl",
      "mfs cat postgres://prod/public/tickets/rows.jsonl --range 0:1000",
      "mfs export postgres://prod/public/tickets/rows.jsonl ./tickets.jsonl"
    ]
  }
}
```

稳定错误码：

| code | 含义 |
|---|---|
| `upload_rejected` | 用户显式 `--no-upload` 但本地路径 + remote profile 触发了 upload |
| `upload_bundle_too_large` | 单 bundle 超过 `max_bundle_size_mb` 阈值 |
| `upload_not_applicable` | shared fs 场景下用了 `--force-upload` |
| `object_too_large_for_cat` | cat 大对象未带 `--range` |
| `is_directory` | 对目录 cat |
| `connector_unhealthy` | connector healthcheck 失败 |
| `density_unsupported` | 对结构化对象用 `--peek/--skim/--deep` |
| `range_unsupported` | 对二进制 / image 对象用 `--range` |
| `tail_unsupported` | 对 `capabilities.tail=false` 的对象用 `mfs tail`（如无稳定排序的集合）；改用 `head` / `cat --range` |
| `chunk_max_exceeded` | 该对象超过 `chunk_max`，部分索引 |
| `local_server_unavailable` | local profile 但本机 server 进程不可达 |
| `field_missing` | connector 数据缺 text_fields 配置的字段 |
| `since_unsupported` | 给不支持时间游标的 connector 传 `--since` |
| `watch_unsupported_on_remote` | remote profile 下用 `mfs status --watch` |
| `sync_already_running` | 同 connector 已有 in-flight sync；返回 `see job <id>` |
| `connector_removing` | connector 正在被 remove，拒绝新 add/sync |
| `op_conflict` | 通用并发拒绝（如 sync 中又来 update_config） |
| `export_too_large` | export 估算 size 超过 `export.max_size`；建议改 `--range` 分批 |
| `unknown_config_key` | `mfs config set` 收到未识别的 key |
| `locator_not_found` | `cat --locator` 给的 locator 在该 object 找不到对应记录 |

## 13. Pipe 行为

Pipe 是普通 unix 字节流——MFS 不在 stdin/stdout 上发明私有协议，不识别"上游来自哪个 source"。这样每个新 connector 不需要做 pipe 元数据适配。

规则：

- 上游 `mfs cat / head / tail / grep / search` 输出纯字节流（默认）或 JSON（`--json`），没有 MFS header
- `mfs search` / `mfs grep` 读 stdin 时**总是把 stdin 当临时文本处理**
- 想限定到具体 source 就**传 path 参数**：`mfs search "..." <path>`，不要通过 pipe 表达
- 无 path 且无 `--all` 且无 stdin：报错

典型用法：

```bash
# 临时搜索 stdin 文本
git log --oneline | mfs search "fix auth"

# 大对象切片后用 jq
mfs cat postgres://prod/public/tickets/rows.jsonl --range 0:100 --json \
  | jq '.items[] | select(.priority == "high")'

# 大对象先导出再处理
mfs export postgres://prod/public/tickets/rows.jsonl ./tickets.jsonl \
  && jq 'select(.priority == "high")' ./tickets.jsonl

# 限定 source 直接用 path 参数，不用 pipe
mfs search "token expiry" ./docs/auth.md
```
