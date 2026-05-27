# MFS 端到端测试矩阵

这份文件是 MFS 的端到端（e2e）测试设计：可测的场景、维度的交叉组合、每个用例的预期行为与当前覆盖现状。用来指导补测试、避免重复、跟踪缺口。

实际已实现的测试在 `server/python/tests/phase*.py` 与 `cli/test_cli_e2e.sh`；本文件的编号（A1、B2、D14…）是**计划用例**，落地时在测试里标注对应编号。

图例：✅ 已覆盖 · 🟡 部分/间接 · ❌ 缺口

基础设施现状（本机，决定「现在就能跑」还是「需先起服务」）：

| 后端 | 状态 |
|---|---|
| Milvus Lite / sqlite / 本地 object store | 现成 |
| Milvus Zilliz Cloud（`ZILLIZ_URI`/`ZILLIZ_API_KEY`） | 现成 |
| Postgres（`postgresql:///mfstest?host=/var/run/postgresql`） | 现成 |
| onnx / fastembed 本地 embedding | 现成 |
| OpenAI（`OPENAI_API_KEY`，embedding + summary + VLM） | 现成 |
| MySQL/MariaDB · MinIO/S3 · Mongo | **需先起服务** |

---

## 0. 怎么读这份矩阵（组合爆炸的应对）

完整笛卡尔积（连接器 8 × 管线 8 × 检索 12 × 后端 32 × 生命周期 6 …）不可能全跑。分三层：

- **L1 全跑**：单维度功能——每个 connector / 每个管线 / 每个命令至少一条「跑通」。
- **L2 pairwise（成对交叉）**：任意两个维度的所有取值对至少出现一次。抓 bug 的甜点区，下面多数交叉表即 pairwise。
- **L3 定向高风险三元组**：经验上易爆的特定组合（如 `Zilliz × 增量删除 × per_namespace`），点测不做全展开。

### 维度轴

| 轴 | 取值 |
|---|---|
| A. Connector / object_kind | file(shared-fs) / file(upload) / postgres·mysql / mongo / message_stream(slack/discord/gmail) / record_collection(github/jira/…) / web / s3 / gdrive / bigquery … |
| B. 处理管线 | code(AST) / md(body) / pdf·docx·html(convert→body) / image(VLM) / structured(row_text+schema_summary) / directory(递归 summary) / binary(metadata-only) |
| C. 检索 | hybrid / semantic / keyword × 作用域(path 前缀 / --all) × --kind 过滤 × collapse |
| D. 后端组合 | Milvus{Lite,Zilliz} · metadata{sqlite,PG} · object store{local,S3} · embedding{openai,onnx} · collection_strategy{shared,per_namespace} |
| E. 生命周期 | 首次 add / 无变化 re-add / 改动 / 删除 / 重命名 / --force-index |
| F. 作业/worker | 异步默认 ↔ --wait / in-process ↔ 独立 worker / 并发 / cancel / 熔断 / stale 重认领 / sync_already_running |
| G. 读命令 | cat(全/--range/--peek/--skim/--locator) / head / tail / export / ls / tree / grep |
| H. 安全 | token / credential_ref(env:·file:) / 脱敏 / 路径穿越 / zip-slip / filter AST |

---

## A. Connector 家族 × 处理管线 → 产出 chunk_kind（A×B）

每格 = 「这个 connector 喂这种内容，应产出哪些 chunk_kind + search/cat 行为」。

| 管线 ↓ / Connector → | file(shared) | file(upload) | postgres/mysql | mongo | github | web | s3 |
|---|---|---|---|---|---|---|---|
| code(AST→body) | ✅ A1 | 🟡 A2 | – | – | ✅ A3 | – | 🟡 A4 |
| md/txt(body) | ✅ | 🟡 | – | – | ✅ | ✅ A5(html→md) | 🟡 |
| pdf/docx/html(convert→body) | ✅ A6 | ❌ A7 | – | – | – | ✅ | ❌ A8 |
| image(VLM→vlm_description) | ✅ A9 | ❌ A10 | – | – | 🟡 | ❌ | ❌ A11 |
| structured(row_text+schema_summary) | – | – | ✅ A12 | ❌ A13 | – | – | – |
| message_stream(thread_aggregate) | – | – | – | – | – | – | – |
| record_collection(record_aggregate) | – | – | – | – | ✅ A14 | – | – |
| directory(递归 directory_summary) | 🟡 A15 | ❌ A16 | ❌ A17 | ❌ | ❌ A18 | ❌ | ❌ A19 |
| binary(metadata-only) | ❌ A20 | ❌ | – | – | – | – | 🟡 |

预期样例：
- **A1** code：search「函数名/逻辑」命中 `body`，cat 原文逐字一致，AST 切分不跨函数。
- **A12** structured：每行一条 `row_text` + 一条 `schema_summary`；`cat --locator <pk>` 取单行；search 命中字段值。
- **A14** github：issue+comments 聚合为 `record_aggregate`，PR diff 作为 code/body。
- **A20** binary：仅 `objects` 表有行、Milvus 无 chunk、`ls` 可见、search 不召回、`cat` 报不可索引。
- **A16/A17/A18**：目录摘要对 upload 模式 / 结构化库表树 / repo 树是否成立（现在只在 file shared 验过）——directory_summary 连接器无关性缺口。

---

## B. 后端组合笛卡尔积（D 内部，pairwise）

D = Milvus{Lite,Zilliz} × meta{sqlite,PG} × store{local,S3} × embed{OAI,onnx} × strategy{shared,per_ns}。pairwise 压成 ~8 条覆盖全部两两对：

| 用例 | Milvus | meta | store | embed | strategy | 预期 | 基础设施 | 现状 |
|---|---|---|---|---|---|---|---|---|
| B1 基线 | Lite | sqlite | local | OAI | shared | 全功能基线 | 现成 | ✅ |
| B2 **Zilliz** | **Zilliz** | sqlite | local | OAI | shared | 与 B1 一致（命名/检索/删除/计数） | 现成 | ❌ |
| B3 PG 后端 | Lite | **PG** | local | OAI | shared | job/task/objects 在 PG 一致 | 现成 | ✅ |
| B4 S3 store | Lite | sqlite | **S3** | OAI | shared | artifact 读写走 S3，cat 正常 | 需 MinIO | 🟡 |
| B5 onnx | Lite | sqlite | local | **onnx** | shared | 无 OAI 也能 index+search | 现成 | ✅ |
| B6 **per_ns** | Lite | sqlite | local | OAI | **per_ns** | 每 ns 独立 collection，跨 ns 不串 | 现成 | ❌ |
| B7 **CS 全栈** | Zilliz | PG | S3 | OAI | shared | 多副本 CS 形态全链路 | 需 MinIO | ❌ |
| B8 **dim 切换** | Lite | sqlite | local | OAI(换 dim) | shared | 换维度→落新 collection 名，旧不动、回滚命中 cache | 现成 | ❌ |

---

## C. 检索交叉（mode × scope × kind × collapse，再 × 后端）

| 用例 | mode | scope | --kind | collapse | 预期 | 现状 |
|---|---|---|---|---|---|---|
| C1 | hybrid | path 前缀 | 全 | 否 | 命中限定子树，dense+BM25 融合 | ✅ |
| C2 | semantic | --all | 全 | 否 | 纯向量，跨 connector | 🟡 |
| C3 | keyword | path | 全 | 否 | 纯 BM25，精确词命中 | 🟡 |
| C4 | hybrid | path | **body** | 否 | 只回 body，不回 summary/vlm | ❌ |
| C5 | hybrid | path | **directory_summary** | 否 | 只回目录摘要（主旨型检索） | ❌ |
| C6 | hybrid | --all | 全 | **是** | 同 object 多 chunk 去重到一条 | ❌ |
| C7 | hybrid | path | 全 | 否 | scope 前缀不越界（`/myXdir` 不命中 `/my_dir`，byte-range 而非 LIKE） | 🟡 |
| C8 | hybrid | path | 全 | 否 | **空 collection**(外部删) → 返回 [] 不报错 | ❌(新守卫) |

---

## D. 生命周期 × Connector 类型（E×A：增量语义差异）

| 操作 ↓ / 连接器 → | file shared | file upload | structured(pg) | github |
|---|---|---|---|---|
| 首次 add | ✅ | ✅ | ✅ | ✅ |
| 无变化 re-add（断言 **0 task / 0 embed**） | 🟡 D1 | 🟡 D2 | 🟡 D3 | 🟡 |
| 改一个文件/行（仅它重建） | 🟡 D4 | 🟡 D5 | 🟡 D6 | – |
| 删除（清 chunk+artifact，不再召回） | 🟡 D7 | ✅ D8 | 🟡 D9 | 🟡 |
| 重命名（chunk_id 重写、**0 重 embed**） | ✅ D10 | 🟡 D11 | – | – |
| --force-index（全重建、cache 命中 0 新 API） | 🟡 D12 | 🟡 D13 | 🟡 | – |
| **增量 × 目录摘要**：改深层文件 → 只重算祖先链摘要，旁系不动 | ❌ D14 | ❌ | – | – |

关键缺口 **D14**：增量与递归摘要的交叉——新功能最该补的正确性点。

---

## E. 读命令 × object_kind（G×B）

| 命令 → / kind ↓ | cat | cat --range | --peek | --skim | --locator | head | tail | export | ls/tree |
|---|---|---|---|---|---|---|---|---|---|
| code/md | ✅ | 🟡 E1 | ✅ | – | – | 🟡 E2 | ❌ E3 | ❌ E4 | 🟡 |
| pdf/docx | ✅ | 🟡 | ✅ | – | – | 🟡 | ❌ | ❌ | 🟡 |
| image | ✅ | – | – | ✅ | – | – | – | ❌ E5 | 🟡 |
| structured row | – | – | – | – | ✅ E6 | ✅ E7 | ❌ | ❌ | 🟡 |
| directory | – | – | – | ✅ E8 | – | – | – | – | 🟡 E9 |

预期样例：E1 cat 一个 >100MB 文件 `--range 5-10` 只读那几行、内存平稳；E3 tail 取尾 N 行；E4 export 落原始字节/产物到本地并 sha 校验；E5 export 图片原始字节。

---

## F. Worker/Job × Ingest × Metadata 后端（并发与恢复）

| 用例 | 组合 | 预期 | 现状 |
|---|---|---|---|
| F1 异步 add(默认) | CLI→server, sqlite, in-process | 立刻回 job_id，status queued→running→succeeded | 🟡(缺 CLI e2e) |
| F2 `--wait` | CLI, 任意后端 | 阻塞轮询到终态，打印 done 计数；失败非零退出 | ❌ |
| F3 独立 worker | `--no-process` 入队 + `mfs-server worker`, PG | worker drain，cursor 仅成功后推进 | ✅ |
| F4 并发多 connector | concurrency>1, **PG**(sqlite 强制 1) | N 个 connector job 并行、互不串 task | 🟡 |
| F5 cancel 中途 | 大 job 进行中 `mfs job cancel` | task 边界停写、标 cancelled、不留半 chunk | ✅ |
| F6 熔断 | 注入连续失败到阈值 | job 中止 `circuit_breaker_tripped`，剩余 task cancelled | 🟡 |
| F7 崩溃恢复 | kill worker 中途 → 重启 | 心跳过期→stale 重认领，task durable 过继 | 🟡 |
| F8 sync_already_running | 同 connector 并发两个 add | 第二个 → 409 | ❌ |
| F9 enumeration 抛错 | connector 枚举阶段权限错 | job→failed，不留 active slot，半 task 清掉 | 🟡 |

---

## G. 多 Connector / Namespace 隔离 + 跨对象 Cache 复用

| 用例 | 组合 | 预期 | 现状 |
|---|---|---|---|
| G1 双 connector 共存 | add /a 和 /b | `status` 两条；search --all 跨召回；scope 各自隔离 | 🟡 |
| G2 remove 隔离 | remove /a | /a 的 chunk 清空，/b 完好 | 🟡 |
| G3 **跨对象 embed 复用** | 两 connector 含同内容文件 | 第二个的 embedding **0 新 API**（cache 按内容寻址命中） | ❌ |
| G4 **per_namespace 隔离** | ns1/ns2 各 add | search 只查本 ns 的 collection，跨 ns 不串 | ❌ |
| G5 同路径双身份 | shared-fs 与 upload 同一 abs 路径 | identity 区分（`file://local…` vs `file://<cid>…`），不互相覆盖 | ❌ |

---

## H. 目录 Summary 参数交叉（× 增量，针对新功能）

| 用例 | enabled | dir_recursive | include_image_desc | per_file/max_input_kb | 预期 | 现状 |
|---|---|---|---|---|---|---|
| H1 关闭(默认) | false | – | – | – | 完全不产 directory_summary，不调 summary LLM | ❌ |
| H2 递归开 | true | true | false | 默认 | 每目录一条、自底向上卷、根含深层概念 | 🟡(engine 级✅) |
| H3 仅根 | true | **false** | false | 默认 | 只产连接器根一条 | ❌ |
| H4 含图片描述 | true | true | **true** | 默认 | 目录摘要输入纳入图片 VLM 描述 | ❌ |
| H5 截断 | true | true | false | **极小值** | 超额被截断，仍产合理摘要、不超预算 | ❌ |
| H6 summary cache | true | true | – | – | 重建命中 cache，**0 新 summary 调用** | 🟡(✅在 phase11_summary) |
| H7 增量旁系不动 | true | true | – | – | 改 /a/x → 只 /a、/ 重算；/b 摘要 indexed_at 不变 | ❌ |

---

## I. 安全 × 入口

| 用例 | 入口 | 预期 | 现状 |
|---|---|---|---|
| I1 无 token | 任意 `/v1/*` | 401；`/healthz` 200 | ❌(仅手测) |
| I2 错 token | `/v1/*` | 401 | ❌ |
| I3 credential_ref env: | connector config | 从环境解析、index 成功 | 🟡 |
| I4 credential_ref file: | config | 读文件解析、strip 换行 | 🟡 |
| I5 缺失 ref | env 未设 | 明确报错，不静默跑 | ❌ |
| I6 secret:/vault: | config | 拒绝（未实现）而非伪装成功 | 🟡 |
| I7 脱敏 | `inspect`/日志 | dsn/token/api_key/session_id 打码 | ❌ |
| I8 路径穿越 | cat/ls `../../etc/passwd` | 拒绝 path_escapes_root | 🟡 |
| I9 zip-slip | upload tar 含 `../` | 400 | ✅ |
| I10 filter AST | index_filter `__import__(...)` | 编译期拒 | ✅ |
| I11 Milvus 表达式注入 | object_uri/connector_uri 含 `"`/`\` | `_lit` 转义，delete/query scope 不破 | 🟡 |

---

## J. 故障注入 / 韧性（错误类型 × 阶段 × 后端）

| 用例 | 注入 | 预期 | 现状 |
|---|---|---|---|
| J1 429/5xx | embedding provider 限流 | retryable，指数退避（capped），最终成功 | 🟡 |
| J2 quota/auth | 401/insufficient_quota | fatal，立即失败，计入熔断 | 🟡 |
| J3 **collection 被外部删** | 索引后 drop | search 返回 []、`remove` 不卡死、count=0（新守卫） | ❌ |
| J4 Milvus 不可达 | 断连 | 任务 retryable 重试，job 不静默成功 | ❌ |
| J5 enumeration 异常 | connector 列举抛错 | job failed + 不留 active slot | 🟡 |
| J6 worker 崩溃 | kill -9 中途 | 重启后 task 过继、不丢、不重复 | 🟡 |
| J7 zero-chunk 重建 | 文件变空/全过滤 | 旧 chunk 被清，不留残留 | 🟡 |
| J8 部分失败 | 105 文件中 1 个坏 | 该 task failed，其余成功，job 计数 ok=104 fail=1 | 🟡 |

---

## K. Schema / 版本 / 配置边界

| 用例 | 组合 | 预期 | 现状 |
|---|---|---|---|
| K1 collection 命名 | 任意 add | `mfs_chunks__v{ver}_d{dim}`（或带 ns） | 🟡 |
| K2 dim 变更 | 换 embedding 维度 | 落新 collection，旧不动（=B8） | ❌ |
| K3 metadata schema 不匹配 | 旧库 + 新 CURRENT_SCHEMA_VERSION | 启动 fail-fast，不静默跑坏 | 🟡 |
| K4 index_filter | `[[objects]]` 表达式 | 只索引匹配行；len()/嵌套 locator 可用 | ✅ |
| K5 indexable=false | object 配置 | 记 metadata、不切 chunk、ls 可见 | 🟡 |
| K6 config update | `mfs add --update` | 改 connector 配置不重注册；配置变更触发重建判定 | ❌ |

---

## L. CLI / HTTP UX

| 用例 | 预期 | 现状 |
|---|---|---|
| L1 async add 文案 | 打印 `queued (job …) … mfs status` | ❌ |
| L2 `--wait` 计数 | 打印 `done: N of M …`，失败非零 | ❌ |
| L3 estimate 零计费 | 外部 connector add 前 prompt，不花 embedding 钱、不留状态 | 🟡 |
| L4 estimate dry-run 清理 | estimate 后 file_state/connector_state 不残留 | 🟡 |
| L5 remove confirm | `mfs remove` 无 `-y` → 确认提示 | 🟡 |
| L6 job 命令 | `mfs job <id>` / list jobs | 🟡 |
| L7 token 自举 | `mfs-server run` 生成 `~/.mfs/server.token`，CLI 同机自动读 | 🟡 |
| L8 `--json` | 各命令 `--json` 输出可解析 | 🟡 |

---

## M. 规模 / 边界（性能与截断语义）

| 用例 | 预期 | 现状 |
|---|---|---|
| M1 大文件 cat --range | 流式、内存平稳、不全量读入 | 🟡 |
| M2 head 大文本 | 流式取头 N 行 | 🟡 |
| M3 chunk_max 截断 | 超 chunk_max → `search_status=partial` | 🟡 |
| M4 grep 线性扫描截断 | 超 `_GREP_LINEAR_SCAN_MAX` → 截断通知 | 🟡 |
| M5 summary 预算截断 | 超 max_input_kb → 截断不溢出（=H5） | ❌ |
| M6 lookup_batch / 微批 | embedding micro-batcher 合并并发调用 | 🟡 |

---

## 实施波次

- **Wave 1（红，跟近期改动/事故强相关）**：B2(Zilliz parity)、C8/J3(collection 丢失守卫)、F1/F2/L1/L2(async+wait CLI e2e)、D14/H7(增量×目录摘要)、H1/H3(summary 开关与 recursive)。
- **Wave 2（核心正确性）**：D1–D13(完整增量矩阵)、C4–C7(kind/collapse/scope 边界)、I1/I2/I7(auth+脱敏)、B6(per_ns)、B8/K2(dim 切换)。
- **Wave 3（隔离/韧性/规模）**：G1–G5(多 connector/cache 复用)、F4/F6/F7/F8(并发/熔断/恢复)、J1/J2/J8、M 系列、E3/E4/E5(tail/export)。
- **Wave 4（需起服务）**：B4/B7(MinIO)、A13(Mongo)、A2–A11 的 upload/S3 管线、MySQL 补充。

---

## 落地覆盖更新（phase13_*）

下面的 e2e 测试已落地并通过，按矩阵编号标注：

| 测试文件 | 覆盖编号 |
|---|---|
| `phase13_resilience_smoke.py` | C8 / J3（collection 被外部删 → search []、count 0、remove 不卡死）|
| `phase13_incr_summary_smoke.py` | D14 / H7（增量改深层文件，只重算祖先链摘要，旁系不动）|
| `phase13_summary_modes_smoke.py` | H1（enabled=false 不产摘要、0 调用）/ H3（dir_recursive=false 只摘根）|
| `phase13_zilliz_parity_smoke.py` | B2（Zilliz add→search→dir summary→remove，独立 per-ns collection）|
| `cli/test_async_add.sh` | F1 / F2 / L1 / L2（异步 add 立即返回 + 后台 drain；`--wait` 阻塞）|
| `phase13_lifecycle_smoke.py` | D1 / D4 / D7 / D12（无变化 0 工作、改、删、force-index cache 命中）|
| `phase13_search_filters_smoke.py` | C4 / C5 / C6 / C7（--kind body/dir_summary、collapse、byte-range scope）+ HTTP ?kind= |
| `phase13_security_smoke.py` | I1 / I2 / I7（401/healthz 放行；dsn/token/api_key/session_id/inline-URI 脱敏）|
| `phase13_namespace_smoke.py` | B6 / G4（per_ns 隔离）/ G3（跨对象 embedding cache 复用）|
| `phase13_dim_switch_smoke.py` | B8 / K2（换 embedding dim → 新 collection，旧的不动）|
| `phase13_multi_connector_smoke.py` | G1 / G2 / F8（多 connector 共存/删除隔离/sync_already_running）|
| `phase13_fault_injection_smoke.py` | J1 / J2 / J8 / F6（瞬时重试、致命不重试、部分失败、熔断）|
| `phase13_readcmds_smoke.py` | E3 / E4 / M1 / M3 / M4（tail、export、cat --range、chunk_max=partial、grep 截断）|
| `phase13_cs_fullstack_smoke.py` | B7（Zilliz + Postgres + S3 + summary 全栈一条龙）|

随测试一并修掉的真实问题：
- HTTP `/v1/search` 与 CLI 未透出 `--kind`（设计有，实现漏）→ 已补 `kind` + `--collapse`。
- `chunk.default_chunk_max` 配置定义了却从未生效（per-object cap 硬编码 1_000_000）→ 已接线。
- Milvus delete/query/search 在 collection 缺失时抛错卡住 remove → 已加 `has_collection` 守卫。
- `MFS_SUMMARY_ENABLED` 环境变量（让目录摘要免配置文件开关，并保证测试不被全局 server.toml 污染）。

**仍阻塞（需起服务）**：A13 Mongo 的 live e2e（mongod 未运行；连接器逻辑已由 `phase10_connectors_unit` 离线覆盖）。其余 S3/MySQL 后端的本机服务可用，已由 `phase11_s3` / `phase10_mysql` 覆盖。
