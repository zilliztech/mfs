# MFS v0.4 实现计划（实施导航 + 进度）

> 本文件是 v0.4 实现的**唯一进度真相**。每完成一块就更新末尾「进度状态」。
> 设计依据：`design/01..10-*.md`（绝对依据，实现遇到歧义以文档为准；文档确有缺漏才补设计，补完同步回 design 注释）。

## 0. 环境与约束（实施时反复确认）

- **机器**：AWS Linux, 8 core, 30G RAM, Ubuntu 22.04 内核。sudo 免密可用。
- **Python**：3.10.12，**用 uv 管理**（`uv sync` / `uv run`，不用 pip 拼接）。
- **Rust**：rustup 安装中（minimal profile）；`source ~/.cargo/env` 后用。CLI（`cli/`）和 `server-rs/`（PyO3）需要。
- **Node**：v24（SDK 生成时用）。
- **PostgreSQL**：未装，CS 模式 / PG 队列测试时 `sudo apt install postgresql` 再装。
- **Milvus 两套，都要测过**：
  - **Zilliz Cloud**：env `ZILLIZ_URI` / `ZILLIZ_API_KEY`。**免费实例 collection 上限 ~5 张**——测试用完即 drop，别堆积；`collection_strategy=shared` 单张即可覆盖大部分。
  - **Milvus Lite 3.0**：`uri=~/.mfs/milvus.db`（本地文件）。较新、可能有潜在 bug，实测对齐。
- **Embedding / LLM / VLM**：env `OPENAI_API_KEY`（官方 endpoint，无 BASE_URL）。`text-embedding-3-small`=dim 1536 已验证可用；VLM/summary 用 `gpt-4o-mini`。
  - ⚠ key 在 `~/.bashrc` 交互段，非交互 shell（systemd/spawn/`uv run`）读不到。测试脚本要么 `bash -ic '...'` 包裹，要么显式 export 传入。
- **第三方 connector SDK**：写需要 API key 的 connector（slack/gdrive/notion/jira/...）时，**必须先查最新 SDK/官方 API 文档或 GitHub 源码确认接口签名与返回**，不凭记忆写。
- **端到端测试范围**：
  - 能端到端测（无需额外 key，或已有 OpenAI/Zilliz）：**file**（含 pdf→md / 图片→VLM / 代码 AST / markdown）、**web**（爬公开站）、**github**（公开 repo）。
  - 需 key 的 connector：先实现（查文档写对接口），**暂不端到端测**。
- **端到端的定义**：跑真实 `mfs` 流程，覆盖文档每个细节 + 健壮性 case（索引中断、换 embedding/converter 模型、重跑、API quota 耗尽、rename、deletion、cancel、force-index、job 继承、Zilliz 与 Lite 切换）。测时**监控各处状态**（metadata DB 行、Milvus chunk 数、artifact/transformation cache、job/task 状态机），断言一致性。不止单元测试。
- **Git**：实现分支 `v0.4-impl`（从 main 分叉，**非** refactor-design-draft）。`origin` = 用户 fork。提交/PR 文本不提 AI/Claude（全局 CLAUDE.md）。每完成一块 commit + push origin v0.4-impl。

## 1. 目标工程结构（design/10 §5）

```
cli/                      # Rust CLI（薄 client，单 binary）
protocol/                 # openapi.yaml / schemas / errors.md（跨语言契约）
server/python/            # PyPI: mfs-server
  src/mfs_server/
    api/                  # FastAPI routes /v1
    server/               # entry: mfs-server run|api|worker|reload
    worker/
    engine/               # 业务编排
    connectors/           # base/registry + file/web/github/postgres/...
    processors/           # document/code/table_rows/message_stream/record_collection/image/binary
    common/               # embedding/summary/vlm/retrieval/export + caching/batching clients
    storage/              # metadata / object_store / queue / milvus / transformation_cache
server-rs/                # Rust PyO3 加速：mfs-scan / mfs-jsonl / mfs-grep（可后置，先纯 Python）
sdks/                     # python/typescript/go/java（从 openapi 生成，后置）
deployments/              # docker/compose/helm（后置）
tests/                    # e2e/ 为主（端到端），辅以 connector contract
design/                   # 设计文档（实现绝对依据）
```

旧 `src/mfs/`（0.3.x）**保留作参考**（embedder/chunker/converter/ast_chunker 等逻辑可借鉴迁移），不直接复用其架构；v0.4 收尾时再决定删除。

## 2. Phase 分解（按依赖 + 尽早可端到端）

### Phase 1 — 地基：config + storage + 数据模型
- `mfs_server` 包骨架 + pyproject（uv）。
- config 加载：`server.toml` 查找链（design/02 §3.1）+ env 覆盖；pydantic settings。
- storage/metadata：SQLite + PG 双后端，建全部表（design/02 §10.1：connectors/objects/artifact_cache/connector_jobs/object_tasks/connector_state/watch_grants/file_state，含本轮新增 objects.search_status/chunk_count/index_error/indexed_at）。迁移用简单 SQL（版本表）。
- storage/object_store：local fs 后端（artifacts/ + uploads/ + files/，按 namespace 切）。
- storage/milvus：MilvusClient adapter，`resolve_collection(ns, strategy)`，schema（design/06 §1：chunk_id/namespace_id/connector_uri(partition_key)/object_uri/locator/lines/content(analyzer)/dense_vec/sparse_vec(BM25 Function)/chunk_kind/metadata/indexed_at），index 配置。Lite + Zilliz 都连通。
- storage/transformation_cache：独立 SQLite（CS 用 PG），schema（design/02 §10.4.1）+ Caching*Client 包装骨架。
- **验收**：能在 SQLite 建全表；能在 Milvus Lite 和 Zilliz 各建一张 mfs_chunks（用完 drop）；chunk_id 公式实现 + 单测。

### Phase 2 — connector 框架 + file connector（本机）+ ingest 骨架
- connectors/base：ConnectorPlugin(ABC) + Capabilities + PathStat/Entry/Range/ObjectChange/SyncOptions/GrepMatch/GrepOptions/HealthStatus + StateStore + ConnectorContext(含 declare_enumeration/object_config_for)（design/07 §3/§4）。
- connectors/registry：URI scheme → plugin。
- connectors/file：本机模式（共享 fs，server 直接读盘）。scan + .gitignore/.mfsignore + stat-first（size+mtime→sha1）+ file_state + rename(inode/sha1) + object_kind_of。
- engine + worker + queue：connector_jobs/object_tasks 状态机，claim（SQLite 事务 / PG SKIP LOCKED），FIFO pick_next_job，per-object 原子（upsert+mark 按 task 边界，design/02 §6.4 本轮改版），job 继承（design/02 §7.1）。
- **验收**：`mfs add ./dir`（先用内部 Python 入口，无需 HTTP/CLI）→ objects 表有行、file_state 有行、job/task 走完 succeeded。

### Phase 3 — chunk/embed/写 Milvus + 两层 cache 闭环
- processors：document(markdown via Chonkie RecursiveChunker)、code(Chonkie CodeChunker)、先打通这两类。
- common/embedding：OpenAI provider + BatchingEmbeddingClient + CachingEmbeddingClient（transformation cache）。
- chunk_id 写入 Milvus（DELETE by object_uri + INSERT），batch。
- artifact cache：converted_md 等（document 真实 md 文件不建，pdf 才建——Phase 6）。
- **验收**：`mfs add ./repo`（含 .md/.py）端到端把 chunk 写进 Milvus（Lite + Zilliz）；transformation cache 命中率可观测；重跑命中。

### Phase 4 — 检索 + 读命令 + HTTP API
- common/retrieval：hybrid(dense+BM25 RRF) / semantic / keyword；跨 partition merge；filter；collapse。
- grep 派发：pushdown → BM25 → 线性扫兜底（design/05 §6）。
- 命令逻辑：ls/tree/cat(--range/--locator)/head/tail/export/search/grep/status。
- api/：FastAPI /v1 路由（design/02 §1 + 03）。
- **验收**：HTTP 起 server，curl/httpx 驱动 search/grep/ls/cat 全通；index_filter(AST 白名单) / chunk_max(partial) / search_status 正确。

### Phase 5 — Rust CLI（薄 client）
- cli/：clap 解析 16 命令、profile(client.toml)、machine-id 探测、HTTP transport、人类输出 + --json envelope。
- `mfs serve`(start/stop/restart/status/logs) 封装本机 mfs-server。
- **验收**：`mfs add . && mfs search ... && mfs cat ...` 真 CLI 端到端。

### Phase 6 — 全 object_kind + web + github connector
- pdf/docx → markitdown converter（CachingConverterClient）；图片 → VLM(gpt-4o-mini)；表格/jsonl 等。
- web connector：static backend(aiohttp+markitdown) + ETag/304 + URL 规范化（design/07 §10.7）。
- github connector：公开 repo（代码树 + issues/pulls，匿名或 token）。
- **验收**：file 全 object_kind（md/py/pdf/png/csv/json）端到端；web 爬公开文档站端到端；github 公开 repo 端到端。

### Phase 7 — 健壮性 / 可靠性 case
- circuit breaker（连续 fatal）、retry 退避、错误码（embedding_quota_exceeded 等）。
- checkpoint（cursor 型）、deletion（declare_enumeration: full diff / incremental skip / explicit）、rename(零 re-embed)、cancel(per-object 边界)、force-index(--all)、staging temp+atomic rename（CS）。
- CS 模式（remote profile + 本地路径 upload flow）——需要时装 PG。
- **验收**：每个 case 有可复现脚本，监控状态断言正确。

### Phase 8 — 端到端全 case 测试矩阵（Zilliz × Lite）
- 把上面所有 case 在 Milvus Lite 和 Zilliz Cloud 两套各跑一遍。
- tests/e2e/ 固化为可重复脚本。

### Phase 9 — server-rs 加速（PyO3）：mfs-scan/mfs-jsonl/mfs-grep（先纯 Python fallback，性能化时替换）

### Phase 10 — 需 key 的 connector（slack/discord/gmail/gdrive/notion/jira/linear/zendesk/salesforce/hubspot/postgres/mysql/mongo/bigquery/snowflake/s3/feishu）：查最新 SDK/API 文档写接口，暂不端到端测。

## 3. 进度状态（持续更新）

- [x] Phase 0：环境诊断、`v0.4-impl` 从 main 分叉、design/ 带入、Rust 1.95 安装、本计划。
- [x] Phase 1：地基（config / ids / metadata-sqlite / object_store / milvus / transformation_cache）— 冒烟测 **25/25，Lite + Zilliz 均通过**（`server/python/tests/phase1_storage_smoke.py`）。
- [x] Phase 2：connector 框架 + file connector + engine/worker/queue — 组件测 14/14 + engine 端到端 10/10（add/幂等/增量）。
- [x] Phase 3：chunk/embed/Milvus + cache 闭环 — index+search e2e **14/14（Lite+Zilliz）**：召回正确 + force-index 0 新 API 调用(cache 全命中)。
- [x] Phase 4：检索(hybrid/semantic/keyword)+读命令(ls/cat/head/tail/grep BM25+linear)+HTTP API(/v1)+mfs-server entry — **search 14/14 / commands 10/10 / API 10/10**。
- [ ] **Phase 6（提前）：pdf/image object_kind + web/github connector**  ← **进行中**
- [x] Phase 5：Rust CLI（clap + reqwest/rustls；add/search/grep/ls/cat/status；$MFS_API_URL）— **CLI e2e ALL PASS**（真 binary→server→检索）。注：reqwest 用 rustls-tls（native-tls 缺系统 OpenSSL）。
- [x] engine **table_rows/record_collection pipeline**（read_records→per_row row_text chunk + text_fields 拼接 + locator(pk) + ObjectConfig 从 [[objects]] 解析）。
- [x] **postgres connector**（asyncpg，结构化模板；本地 PG 端到端 7/7：per_row + locator + search）。PG14 装好、test db `mfstest`。注：asyncpg cursor 需在 transaction 内；dsn `postgresql:///mfstest?host=/var/run/postgresql`（peer auth）。
- [ ] Phase 10 余：需 API key 的 SaaS connector（slack/discord/gmail/gdrive/notion/jira/linear/zendesk/salesforce/hubspot/feishu/mysql/mongo/s3/bigquery/snowflake）— 查最新 SDK/API 文档写、**暂不端到端测**（无 key）。
- [ ] 其余：SDK(py/ts/go/java 从 openapi 生成)、deployments(docker/helm)、Skill bundle、server-rs 加速（PyO3）、cancel(daemon)。
- [ ] Phase 7：健壮性 case
- [ ] Phase 8：端到端矩阵（Zilliz × Lite）
- [ ] Phase 9：server-rs 加速
- [ ] Phase 10：需 key 的 connector

### 当前 context 交接笔记
（每次 context 结束前更新：做到哪、下一步、踩的坑）

**已完成 Phase 1**。代码在 `server/python/src/mfs_server/`（config.py / storage/{ids,metadata,milvus,object_store,transformation_cache}.py）。用 `cd server/python && uv run python tests/phase1_storage_smoke.py` 验证。

**关键实测发现（务必记住，避免返工）**：
- `milvus-lite==3.0` 已装为核心依赖（正是用户要的 3.0）。
- **Milvus Lite 不支持 scalar INVERTED 索引**（报 `missing metric_type`）。已改为：create_collection 只带 dense(HNSW/COSINE)+sparse(SPARSE_INVERTED_INDEX/BM25) 两个向量索引；scalar 索引在 `MilvusStore._add_scalar_indexes` best-effort 后建、`except: pass` 容错。filter 无 scalar 索引时全扫（小数据无碍）。
- Milvus search 必须带 `consistency_level="Strong"`（Zilliz serverless 默认 Bounded，否则刚写的查不到）；写后测试 `time.sleep(2)`。
- `chonkie==1.6.7`：`chunker(text)` 直接调用返回 `list[Chunk]`；`Chunk.text / .start_index / .end_index`（**字符偏移，非行号！**Phase3 转 lines 要 `content[:start].count("\n")+1`）`/ .token_count`。RecursiveChunker(tokenizer='character'|str, chunk_size, ...)；CodeChunker(language='auto')。
- `markitdown`：`MarkItDown().convert(path_or_stream).text_content`（也有 `.markdown` / `.title`）。
- Zilliz 免费实例：测试用 shared 单张 `mfs_chunks`，**用完即 drop**（smoke test 已自动 drop 清理）。
- Milvus 配置从 env：`MFS_MILVUS_URI/TOKEN` 优先，回落 `ZILLIZ_URI/ZILLIZ_API_KEY`（这俩在全局 env、非交互可读）；OpenAI key 在 `~/.bashrc` 交互段，跑需 OpenAI 的测试要 `bash -ic '...'` 或显式 export。
- pymilvus 同步 API；engine/worker 用 `asyncio.to_thread` 包装。
- **跑测试用 `cd server/python && timeout 60 .venv/bin/python tests/XXX.py` 前台**，不要 `uv run python`（会被 harness 判后台，且多进程抢同一临时 sqlite 会 hang）。需 OpenAI key 的测试用 `bash -ic`。每个测试用唯一/先清理的 db 路径避免竞争。
- file_state.connector_id 有 FK→connectors(id)：测试/流程要先插 connectors 行再 sync。

**Phase 2 完成**：`base.py`（契约 + ConnectorContext + on_object_indexed/deleted hook）、`registry.py`、`storage/file_state.py`、`connectors/file/plugin.py`（本机 scan/ignore/stat-first/rename/sync）、`engine/engine.py`（register_or_get_connector / add / _run_job / _index_object **stub** / _claim_batch / job 继承）、`engine/state.py`（ConnectorStateStore）。`object_tasks` 加了 `old_uri` 列。测试：file 组件 14/14、engine 端到端 10/10（`tests/phase2_*_smoke.py`）。Milvus Lite 跳过 scalar 索引（避免噪音 + 不支持）。

**当前 `_index_object` 是 Phase 2 stub**：只 stat + 写 objects/file_state，chunk_count=0，不 chunk/embed/写 Milvus。Phase 3 填真逻辑。

**Phase 3 完成**：`common/embedding.py`（CachingEmbeddingClient：OpenAI + tx cache memo，float32 packed，api_calls/cache_hits 监控）、`processors/text.py`（chunk_body：Chonkie Recursive/Code，字符偏移→行号）、engine `_index_object` 真实（read→chunk→embed→`delete_by_object`+`upsert`，per-object 原子，chunk_count/search_status）。测试 14/14（Lite+Zilliz）。
- **count/search 都用 `consistency_level="Strong"`**（Zilliz serverless 必须，否则刚写查不到）。
- rename Phase 3 暂当重 embed；chunk_id-rewrite 复用向量优化留 Phase 7。
- image(VLM)/pdf(converter)/text_blob 索引留 Phase 6。

**下一步 Phase 4：检索 hybrid + 读命令 + HTTP API**：
- ✅ milvus.py `hybrid_search`/`sparse_search` 已加 + **pymilvus API 已 Lite 验证**：keyword=`client.search(data=["text"], anns_field="sparse_vec")`（BM25 Function 自动转 sparse）；hybrid=`client.hybrid_search(reqs=[AnnSearchRequest(data=[qvec],anns_field="dense_vec",param={"metric_type":"COSINE"}), AnnSearchRequest(data=["text"],anns_field="sparse_vec",param={})], ranker=RRFRanker())`。BM25 distance 可为负，正常。
- ✅ `common/retrieval.py`（build_filter/to_envelope/collapse_by_object）+ `engine.search`（hybrid/semantic/keyword）。**search modes e2e 14/14（Lite+Zilliz）**：召回正确、session>README、BM25 'redis' 命中、collapse 去重、envelope 格式对。
- ✅ grep 派发(engine.grep)：BM25 主路径 + linear fallback（not_indexed 走 connector.read 扫，行号）。commands e2e 10/10。pushdown 待结构化 connector(Phase 6)。
- ✅ 读命令(engine 方法)：ls / cat(--range/--meta) / head / tail。cat --locator 待结构化(Phase 6)；export 简单可补；tree/status 待补。
- ✅ `api/app.py` FastAPI /v1（server/info,add,search,grep,ls,cat,status,jobs）+ `server/__main__.py`(run/api 起 uvicorn；worker/reload stub)。HTTP API e2e 10/10。

**Phase 4 完成**。server 端 add→index→search/grep/cat 全链路通（HTTP + 直接调）。

**优先级调整**：Rust CLI(Phase 5) 后置——HTTP API 已能驱动全部 e2e；先补 server 端 connector 能力（用户核心诉求 file/pdf/图片/web/github 端到端 + 健壮性）。

**下一步 Phase 6：object_kind 扩展 + web/github**：
1. ✅ `common/converter.py` CachingConverterClient（markitdown[all]，tx cache kind='convert'）+ converted_md artifact + cat 返回 md。**pdf/html e2e 10/10（Lite+Zilliz）**。注：**markitdown[all] 已装**（基础 markitdown 不含 pdf 依赖）；CONVERT_EXTS={pdf,docx,doc,pptx,ppt,xlsx,xls,html,htm}；fpdf2(dev) 生成测试 pdf。
2. ✅ `common/vlm.py`（gpt-4o-mini vision，image_url base64 data URL — 已验证）→ vlm_description chunk + vlm_text artifact + cat 返回描述。**image e2e 8/8（Lite+Zilliz）**。至此 file connector 全 object_kind（md/code/pdf/docx/html/image）端到端通过。
3. ✅ `connectors/web/`（aiohttp 爬 + markitdown 内联转 + ETag/304 + URL 规范化 + page md 存 converted_md artifact）+ engine.add(config=) 支持 + _resolve_target web/github。**web e2e 4/4（爬 example.com）**。注：`bash -ic` 跑测试要在 **bash -ic 内部 `cd /abs/server/python`**（交互 shell cwd 会漂移到 home）。web cat/ls 待 _open_path 支持 scheme URI（下轮）。
4. ✅ `connectors/github/` 公开 repo code tree（`trees?recursive=1` + raw.githubusercontent + **GITHUB_TOKEN env**，匿名 rate-limit 已耗尽必须带 token）。**github e2e 5/5**（octocat/Spoon-Knife）。issues/pulls 待补。

**Phase 6 端到端 connector 全部完成**：file（md/code/pdf/docx/html/image）+ web + github，对外要求的都端到端通过（多数 Lite+Zilliz 双测）。

**下一步 Phase 7：健壮性 / 可靠性 case（用户重点）**：
1. ✅ 换模型 force-index：cache 失效重 embed（version bump 模拟）+ 再跑命中。
2. ✅ deletion：删文件 → deleted task → objects/Milvus 删 + search 不返回。
3. ✅ 索引失败恢复：task failed + 该 object 不 indexed + 其他 task 不受影响 → 下次 add 恢复 indexed（file_state staged 重 yield + job 继承）。**robustness e2e 12/12**。
4. ✅ circuit breaker/quota：`_process_with_retry`（错误分类 fatal=quota/auth vs retryable + 退避重试）+ `_run_job` 连续 fatal abort job（cancel 剩余 pending/running，error='circuit_breaker_tripped'）。**robustness e2e 15/15（A/B/C/D）**。
5. (剩余，次要) cancel：per-object 边界 —— 同步 add 模型下无并发 cancel 场景，留到 Phase 5+ 后台 daemon。
6. ✅ rename 零 re-embed：`milvus.get_chunks_by_object` → 改 chunk_id（新 uri）→ upsert + 删旧 + move artifact。**rename e2e 7/7（零新 embedding 调用，chunk count 不变）**。
- 之后 Phase 8 矩阵、Phase 5 Rust CLI、Phase 10 需 key connector（查 SDK 文档写、不端到端测）。
- 注意：web/github cat/ls 需 _open_path 支持 scheme URI（目前 file-only）；可在 Phase 7/收尾补。
- index_filter(AST 白名单) 属结构化 per_row 场景，留 Phase 6。
