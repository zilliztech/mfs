# Search 与 Retrieval

这一篇讲 Milvus collection schema 长什么样、每个 connector 写什么进去、用户怎么配字段、search 流程怎么走。

## 1. Milvus collection schema

collection 布局由 server.toml 的 `collection_strategy` 决定（`shared` 默认 / `per_namespace`，见 [02 §9.4](02-architecture.md#94-milvus-隔离collection_strategy)）。**两种策略字段定义、partition_key、chunk_id 公式完全一致**，唯一差别是 collection 命名和 namespace 隔离机制。

字段定义（两种策略共用）：

```python
from pymilvus import MilvusClient, DataType, Function, FunctionType

client = MilvusClient(uri=cfg.milvus_uri, token=cfg.milvus_token)

# ── schema：两种 collection_strategy 共用同一份字段定义 ──
schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
schema.add_field("chunk_id",      DataType.VARCHAR, max_length=128, is_primary=True)
schema.add_field("namespace_id",  DataType.VARCHAR, max_length=64)            # shared 下做 scalar filter；v0.4 恒 "default"
schema.add_field("connector_uri", DataType.VARCHAR, max_length=256, is_partition_key=True)
schema.add_field("object_uri",    DataType.VARCHAR, max_length=1024)
schema.add_field("locator",       DataType.JSON, nullable=True)              # 结构化对象的单元 key（row pk / thread_ts / number），无则 null
schema.add_field("lines",         DataType.JSON, nullable=True)              # body/code/document 的 [start, end]，无则 null；进 chunk_id 区分同文件多 chunk
schema.add_field("content",       DataType.VARCHAR, max_length=65535, enable_analyzer=True)  # 召回展示 + BM25 输入（开 analyzer 供下面 BM25 function 用）
schema.add_field("dense_vec",     DataType.FLOAT_VECTOR, dim=N)              # N 来自 embedding model
schema.add_field("sparse_vec",    DataType.SPARSE_FLOAT_VECTOR)             # 由下面的 BM25 Function 从 content 自动生成
schema.add_field("chunk_kind",    DataType.VARCHAR, max_length=32)
schema.add_field("metadata",      DataType.JSON, nullable=True)             # connector-specific filter 字段
schema.add_field("indexed_at",    DataType.INT64)

# content → sparse_vec 的 BM25 走 Milvus 内建 Function：写入只给 content，sparse_vec 自动算
schema.add_function(Function(
    name="content_bm25",
    function_type=FunctionType.BM25,
    input_field_names=["content"],
    output_field_names=["sparse_vec"],
))

# ── index ──
index_params = MilvusClient.prepare_index_params()
index_params.add_index(field_name="dense_vec",  index_type="HNSW",
                       metric_type="COSINE", params={"M": 16, "efConstruction": 200})
index_params.add_index(field_name="sparse_vec", index_type="SPARSE_INVERTED_INDEX",
                       metric_type="BM25")
for f in ("namespace_id", "object_uri", "chunk_kind"):     # 高频 filter 字段建标量倒排索引
    index_params.add_index(field_name=f, index_type="INVERTED")

# ── 唯一的分叉点：写/查哪张 collection ──
def resolve_collection(namespace_id: str, strategy: str) -> str:
    if strategy == "per_namespace":
        return f"mfs_chunks__{namespace_id}"   # 每 namespace 一张
    return "mfs_chunks"                        # shared：全局一张

client.create_collection(
    collection_name=resolve_collection(namespace_id, cfg.collection_strategy),
    schema=schema,
    index_params=index_params,
)
```

`connector_uri` 用 `is_partition_key=True` 声明，写入/查询由 Milvus 按它自动哈希分桶（桶数 `num_partitions` 默认 64）；`sparse_vec` 由 BM25 `Function` 从 `content` 自动派生——worker 写入只提供 `content` + `dense_vec`，不手动塞 sparse 向量。

### 两种策略的差异（只有这两点）

| | shared（默认）| per_namespace |
|---|---|---|
| collection | 一张 `mfs_chunks` | 每 namespace 一张 `mfs_chunks__<ns>` |
| namespace 隔离靠 | `namespace_id` scalar filter | collection 物理边界 |
| 查询 filter | `namespace_id IN (...) AND connector_uri == X ...` | `connector_uri == X ...`（collection 已隔离 ns）|
| 同名 connector 跨 ns 撞桶 | 有（scalar filter 兜底正确性）| 没有（不同 collection）|
| partition_key / 字段 / chunk_id | — | **全部相同** |

`namespace_id` 字段在 `per_namespace` 下冗余（恒等于 collection 对应的 ns），但保留——让 write / processor / chunk_id 代码两种策略零分叉，分叉只发生在 `resolve_collection` 这一个点。chunk_id 公式两种策略都含 namespace_id，迁移时 chunk_id 也稳定。

### Partition by connector_uri（用 `partition_key`，不是 named partition）

`partition_key=connector_uri` 是 schema 阶段必须定死的字段，Milvus 按它自动哈希分桶。**为什么选 connector_uri 不选 namespace_id**（dominant query / v0.4 分片 / 数据倾斜三个理由）详见 [02 §10.3](02-architecture.md#103-milvus)。

partition_key 带来的加速：

| 操作 | partition_key 带来的好处 |
|---|---|
| `mfs search "..." postgres://prod` | filter 带 `connector_uri == X` → 只扫该 connector 命中的物理桶 |
| `mfs search "..." --all` | 多桶并行扫，scatter-gather |
| `mfs connector remove postgres://prod` | `DELETE WHERE connector_uri = X` 按 partition_key 路由，只扫命中桶 |

> 精确说：`num_partitions` 默认 64 桶，connector 上千时多个 connector_uri 会哈希进同一个桶。所以"只扫命中桶"是把扫描量缩到约 1/64，桶内仍混着其他 connector 的行，靠 `connector_uri == X` scalar filter 精确过滤——不是一 connector 一桶。正确性由 scalar filter 兜，性能由分桶 + 该字段的 INVERTED 索引兜。

后改 partition key 需要数据迁移，所以一开始定下来。

### namespace_id 字段：shared 下做 scalar filter

`shared` 策略下所有 chunk 带 `namespace_id`，查询自动 filter `namespace_id IN (current_request_namespaces)`。v0.4 server 只有一个 `default` namespace，所有 chunk 都写 `"default"`，client 不感知。两个 namespace 注册同名 connector 会落到同一物理桶，靠 `namespace_id` scalar filter + chunk_id 含 namespace_id 防撞做隔离。

需要强隔离（合规 / SaaS multi-tenant）就用 `per_namespace` 策略——v0.4 就能选，部署时定，详见 [02 §9.4](02-architecture.md#94-milvus-隔离collection_strategy)。

Workspace / User 等组织概念不进 Milvus schema——它们通过 server 端 mapping 表换算成 namespace_id 集合后再 filter。详见 [02 §9](02-architecture.md#9-多租户与-namespace)。

### 字段说明

| 字段 | 含义 |
|---|---|
| `chunk_id` | `sha1(namespace_id + connector_uri + object_uri + chunk_kind + locator + lines)`，幂等写入。namespace_id 必须进 hash——否则两个 namespace 注册同名外部数据源会让 chunk_id 撞车。`locator` / `lines` 区分同一 object 内的多个 chunk：结构化对象（row / thread / issue）用 `locator`，`body` / code / document 这类 `locator` 为 null 的对象用 `lines`（`[start, end]` 行区间）——否则同一文件的多个 `body` chunk 会撞键互相覆盖（详见 [02 §7](02-architecture.md#7-一致性)）|
| `namespace_id` | 物理分区主键；v0.4 恒为 `"default"`，多租户启用后由 server 注入 |
| `connector_uri` | 包含该 chunk 的 connector root，如 `postgres://prod` |
| `object_uri` | chunk 来自哪个 object，如 `postgres://prod/public/tickets/rows.jsonl` |
| `locator` | object 内单元定位，per-connector schema（见 §3）；`body`/code/document 这类按行切的对象为 null |
| `lines` | `[start, end]` 行区间；`body`/code/document chunk 用它定位 + 进 chunk_id 区分同文件多个 chunk；结构化对象（row/thread）为 null |
| `content` | chunk 文本：dense embed 输入 + BM25 输入 + 召回展示 |
| `dense_vec` | embedding 向量 |
| `sparse_vec` | Milvus 内置 BM25 sparse 向量 |
| `chunk_kind` | 8 种之一（见 §2） |
| `metadata` | filter / 展示用的 JSON：`{status, priority, author, updated_at, ...}` |
| `indexed_at` | unix ms |

## 2. chunk_kind 枚举（framework 固定）

固定 8 种，connector 不能私加：

| chunk_kind | 来源 | 例 |
|---|---|---|
| `body` | document / code 的正常段落 | 一段 markdown / 一个函数体 |
| `row_text` | DB row / SaaS record / 单条 issue | 单条 ticket 拼接字段后的文本 |
| `thread_aggregate` | 多消息聚合 | Slack thread / Discord forum thread / Gmail thread |
| `record_aggregate` | record 含子项 | GitHub issue 含 comments / Zendesk ticket 含 comments |
| `summary` | 显式 LLM summary | 文件 / record / thread 的 LLM 摘要 |
| `vlm_description` | 图片描述 | png / jpg / 表情图 |
| `directory_summary` | 目录概览 | 一个目录的功能描述 |
| `schema_summary` | 表/集合 schema 推导描述 | postgres 表 + 业务字段的语义描述 |

新增 chunk_kind 要走框架升级流程，不通过 connector TOML 加。

search 默认全 kind 召回，用 `mfs search ... --kind body,summary` 限定。

## 3. locator schema per connector

每个 connector 的 `locator` JSON schema 是稳定契约，agent 按这个 parse。`locator` 为 null 的对象用独立的 `lines` 字段（`[start, end]`）定位 + 进 chunk_id（见 §1）：

| Connector | locator schema |
|---|---|
| `file` | `null`（用 `lines` 字段 `[start, end]`） |
| `github code` | `null`（同 file） |
| `web` | `null`（页面级；用 `lines`） |
| `github issues / pulls` | `{"number": int}` |
| `gdrive` | `{"file_id": str, "revision_id": str}` |
| `s3 / r2 / gcs` | `null`（同 file） |
| `slack` | `{"channel": str, "date": str, "thread_ts": str}` |
| `discord` | `{"channel_id": str, "thread_id": str}` |
| `gmail` | `{"thread_id": str}` |
| `postgres / mysql` | `{"schema": str, "table": str, "pk": {<pk_fields>}}` |
| `mongodb` | `{"db": str, "collection": str, "_id": str}` |
| `bigquery / snowflake` | `{"dataset": str, "table": str, "partition": str, "pk": {...}}` |
| `linear` | `{"team": str, "id": str}` |
| `jira` | `{"project": str, "key": str}` |
| `notion` | `{"database_id": str, "page_id": str}` |
| `zendesk` | `{"object": str, "id": int}`  ── e.g. `{"object":"tickets","id":123}` |
| `salesforce / hubspot` | `{"object": str, "id": str}` |

要打开单条对象用 `mfs cat <source> --locator '<json>'`（按 locator 精确取回完整记录，见 [03 §6](03-cli-commands.md#6-ls--tree--cat)），或 `mfs export <source> ./tmp.jsonl && jq 'select(.id == 12)'`。

## 4. 字段配置

`connector TOML` 的 `[[objects]]` 段配三类字段 + chunk_strategy：

```toml
[[objects]]
match = "public.tickets"
text_fields       = ["subject", "description", "comments[].body"]
metadata_fields   = ["status", "priority", "assignee", "updated_at"]
locator_fields    = ["id"]
chunk_strategy    = "per_row"        # per_row | per_group | per_field_chunked | windowed | sampled

# 选填
group_by          = "thread_ts"      # chunk_strategy="per_group" 时
session_idle_min  = 10               # group_by="session" 时
text_template     = "..."            # 覆盖默认拼接模板（jinja-style）
max_text_chars    = 8000             # 单 chunk 上限，超出自动转 per_field_chunked
index_filter       = "state == 'open'" # 只索引部分记录
chunk_max         = 100000           # 索引行数硬上限
indexable         = true             # 设 false 完全不进 Milvus
```

### text_fields

进 `chunk.content`。embedding 和 BM25 都用这份文本。

多字段时按默认模板拼接：

```
{field1_name}: {field1_value}

{field2_name}: {field2_value}
...
```

数组字段（`comments[].body`）按列表展开：

```
comments:
- {body_1}
- {body_2}
```

### metadata_fields

进 `chunk.metadata`。用于 search filter（`--filter status=open`）和结果展示。null 值跳过。数组用 `[*]`（如 `labels[*]`）展开。

### locator_fields

从 record 取值组成 `locator` dict。多字段时按字段名做 key：`locator_fields=["schema","table","id"]` → `{"schema":"public","table":"tickets","id":12}`。

### JSONPath 表达式（简化子集）

| 表达 | 含义 |
|---|---|
| `subject` | 直接字段 |
| `user.email` | 嵌套 |
| `comments[]` | 数组所有项（必须配 `.field` 后缀取 dict 内字段） |
| `comments[].body` | 数组每个元素的 body 拼接 |
| `comments[0:5].body` | 前 5 个 |
| `labels[*]` | 数组扁平 join |

只支持这 5 种。复杂表达请 connector plugin 在 fetch 时 pre-join。

### chunk_strategy

| strategy | 适用 | 一个 chunk 是 |
|---|---|---|
| `per_row` | issue / ticket / DB row / SaaS record | 一条记录 |
| `per_group` | slack / discord / gmail | 一组（thread / session / time-window） |
| `per_field_chunked` | 单字段长文本（如 Notion page body、web page） | 该字段按 markdown 切多个 chunk |
| `windowed` | 超大时序表只索引近窗口 | 一条记录（见下）|
| `sampled` | 超大 audit / event 表抽样索引 | 一条记录（见下）|

前三个决定"一个 chunk 是什么形态"；`windowed` / `sampled` 是在 `per_row` 基础上叠加"只索引哪部分行"的**行子集选择**（仍按行切，配 `chunk_window` / `sample_rate`），用于大对象降量，详见 [§11](#11-大对象索引控制)。`per_field_chunked` 也是 fallback：text 超过 `max_text_chars` 时自动从 `per_row` 升级。

### index_filter

只索引部分记录，避免 chunk 爆炸。是一个**受限表达式**，由 framework 解析成 AST 后在白名单内求值——**不是 `eval` Python**（Python sandbox 几乎做不牢，远端 server 上执行不可信配置太危险）：

```toml
index_filter = "state == 'open' and priority in ['high', 'critical']"
index_filter = "updated_at > '2025-01-01'"
index_filter = "len(description) > 50"
```

白名单只放：字段引用、字面量（str / num / bool / list）、比较 `== != < > <= >=`、布尔 `and / or / not`、成员 `in`，以及少量安全内建（`len`）。属性访问 / 任意函数调用 / 下标 / 推导式一律在**解析期**拒绝（报 `invalid_index_filter`），不进求值。

> **别跟 `mfs search --filter`（[§7](#filter)）混**：`index_filter` 是 **ingest 时**筛"哪些 record 进 Milvus"，由 MFS 在 server 端对**原始 record** 求值；`--filter` 是 **query 时**筛"已索引 chunk 哪些命中返回"，翻译成 **Milvus 原生 expr** 作用在 `metadata` 字段上。两者时机、作用对象、执行引擎都不同，语法也不通用（如 `len()` 只有 `index_filter` 能用，Milvus expr 不支持）。

### chunk_max

硬上限。达到上限就停止生成更多 chunk——**已生成的 chunk 保留并索引（部分索引，不是整个对象不索引）**，同时报 `chunk_max_exceeded` 让用户看到选择：要么加 `index_filter`，要么开 windowed 策略。该对象 `search_status` 标 `partial`，search 仍可用但召回不全。

## 5. 内置 preset

公共 SaaS / 消息 connector 自带 preset，用户不配也能跑：

```python
# framework 内置（伪代码）
PRESETS = {
    "github.issues": dict(
        text_fields=["title", "body", "comments[].body"],
        metadata_fields=["state", "labels[*]", "author", "assignees[*]", "updated_at"],
        locator_fields=["number"],
        chunk_strategy="per_row",
    ),
    "github.pulls": dict(
        text_fields=["title", "body", "reviews[].body", "comments[].body"],
        metadata_fields=["state", "draft", "labels[*]", "author", "merged_at", "updated_at"],
        locator_fields=["number"],
        chunk_strategy="per_row",
    ),
    "slack.messages": dict(
        chunk_strategy="per_group",
        group_by="thread_ts",
        fallback_group_by="session",
        session_idle_min=10,
        text_fields=["text"],
        metadata_fields=["channel", "users[*]", "start_ts", "end_ts"],
        locator_fields=["channel", "date", "thread_ts"],
    ),
    "discord.messages": dict(
        chunk_strategy="per_group",
        group_by="thread_id",
        text_fields=["content"],
        metadata_fields=["channel_id", "users[*]"],
        locator_fields=["channel_id", "thread_id"],
    ),
    "gmail.messages": dict(
        chunk_strategy="per_group",
        group_by="thread_id",
        text_fields=["subject", "body"],
        metadata_fields=["from", "to[*]", "cc[*]", "date", "labels[*]"],
        locator_fields=["thread_id"],
    ),
    "zendesk.tickets": dict(
        text_fields=["subject", "description", "comments[].body"],
        metadata_fields=["status", "priority", "requester_id", "assignee_id", "tags[*]", "updated_at"],
        locator_fields=["id"],
        chunk_strategy="per_row",
    ),
    "linear.issues": dict(
        text_fields=["title", "description", "comments[].body"],
        metadata_fields=["state", "team", "assignee", "labels[*]", "updated_at"],
        locator_fields=["team", "id"],
        chunk_strategy="per_row",
    ),
    "jira.issues": dict(
        text_fields=["summary", "description", "comments[].body"],
        metadata_fields=["status", "priority", "assignee", "labels[*]", "updated_at"],
        locator_fields=["key"],
        chunk_strategy="per_row",
    ),
    "web.page": dict(
        text_fields=["title", "body"],
        metadata_fields=["url", "domain", "fetched_at"],
        locator_fields=["url"],
        chunk_strategy="per_field_chunked",     # body 长，按 markdown 切
    ),
    # ... salesforce.account / hubspot.contact / 等
}
```

Postgres / MySQL / MongoDB / 用户自定义 SaaS 对象没有 preset——字段都是业务定义的，必须显式配。缺失时报错：

```text
Connector postgres://prod registered.
Warning: public.tickets has no text_fields configured.
search/grep will be unavailable for this object until you add:

  [[objects]]
  match = "public.tickets"
  text_fields = ["..."]
  locator_fields = ["id"]
```

## 6. 各 object_kind 的 chunk 来源

每类 object 进 Milvus 的内容是什么样的：

| object_kind | 进 Milvus 的 chunk_kind | 每条 chunk 是 |
|---|---|---|
| `document` (md/pdf/docx/gdoc/html→md) | `body` + 可选 `summary` | 一个 markdown 段落 / 一段抽取文本 |
| `code` | `body` | 一个函数 / 一个 class / 一段 region（AST 切分） |
| `image` | `vlm_description` | VLM 给出的描述文本 |
| `text_blob` (json/csv/log) | 默认不进 | 不索引；grep 兜底 |
| `binary` | 不进 | metadata only |
| `table_rows` (rows.jsonl) | `row_text` | 按 text_fields 拼接的单行 record 文本 |
| `table_schema` (schema.json) | 可选 `schema_summary` | LLM 推导的 schema 描述 |
| `message_stream` (messages.jsonl) | `thread_aggregate` + 可选 `summary` | 一个 thread / session 的对话拼接 |
| `record_collection` (issues.jsonl/records.jsonl) | `record_aggregate` 或 `row_text` | 一条 record 拼字段 |
| `directory` | 可选 `directory_summary` | LLM 给出的目录功能描述 |

### chunker 实现选型

`body` chunk（document / code 正文切分）的切块器是 framework 内置的（贡献者碰不到，见 [07 §0](07-contributing-connector.md#0-必须实现-vs-可选重写)）：

- **纯文本 / markdown** → **Chonkie** `RecursiveChunker`：自带 markdown recipe，且 **token-aware**——tokenizer 跟 embedding 用的对齐，切块边界吃满 token 预算，不像字符切分那样跟 embedding 口径错位。
- **代码** → Chonkie `CodeChunker`（底层 tree-sitter、多语言），跟文本共用一套依赖和 tokenizer 口径，不自己接 tree-sitter。切出来是一个函数 / 一个 class / 一段 region。代码切分必须兜住边角：
  - 单个 AST 节点就超 chunk_size（巨型函数）→ 节点内按子节点 / 行递归下钻，不整块塞
  - 解析失败（语法错误 / 不支持的语言）→ fallback 到 recursive / 按行硬切，不丢文件
  - minified / 单行巨型文件 → AST 退化，按 token 硬切
  - 超小文件 → 整文件一个 chunk

chunker 用第三方库（Chonkie），库版本在 `pyproject.toml` pin 死、保证可复现。chunker **不进 cache、不算指纹**——它便宜、重跑就行，升级影响通过"切出的 text 变了 → embed 的 cache key 变了"自然传导（见 [04 §5.2](04-connector-and-ingest.md#52-重建与-cache)）。

## 7. Search 流程

```
mfs search "..." <path> --top-k 10
  │
  ├─ 1. embed query → dense_vec
  │
  ├─ 2. 解析 <path>:
  │     - 本地路径 → file connector 的 URI
  │     - URI → 解析 partition + path prefix
  │     - --all → 全 partition
  │
  ├─ 3. Milvus hybrid search:
  │     filter = {
  │       namespace_id  IN current_namespaces,
  │       connector_uri in [<partition>],
  │       object_uri    LIKE '<prefix>%' (optional),
  │       chunk_kind    in [...] (optional --kind),
  │       metadata.<field> = ... (optional --filter)
  │     }
  │     ranker  = RRF(dense_score, sparse_score)
  │     limit   = top_k * over_fetch_ratio per partition
  │
  ├─ 4. 后处理:
  │     - 跨 partition merge：按 RRF score 全局排序，取 top_k
  │     - 同 object_uri 去重（可选 --collapse object）
  │
  └─ 5. 渲染：{source, locator, content, score, metadata}
```

### 跨 partition 合并语义

`mfs search --all` 或 `mfs search <path>` 跨多个 connector 时：

- 每 partition 取 `top_k * over_fetch_ratio`（默认 ratio=3），避免漏掉某个 partition 真正高分的结果
- RRF 融合分数跨 partition 可比：RRF 公式 `1/(k + rank)` 跟绝对相似度无关，只看 partition 内的排名。理论上仍有些 bias（小 partition 排名分布密），但实测对 hybrid 召回影响小
- 全局 merge：拿到所有 partition 的 over-fetch 结果后按 RRF score 排序，取全局 top_k 返回

`over_fetch_ratio` 在 server.toml 配置：

```toml
[search]
over_fetch_ratio = 3              # 跨 partition 时每 partition 取 top_k * 3
max_partitions_per_query = 32     # --all 时最多并行扫几个 partition
```

如果 connector 多到几十几百，跨全部 partition 查会慢，建议用 `--connector-type` filter 限定（如 `mfs search "..." --all --connector-type postgres,slack`）。

### 模式

```bash
mfs search "..." <path>                    # hybrid（默认）
mfs search "..." <path> --mode semantic    # 仅 dense 向量（语义）
mfs search "..." <path> --mode keyword     # 仅 BM25 sparse（关键词）
```

模式名 `hybrid` / `semantic` / `keyword` 是面向用户的叫法；底层对应 `dense_vec` + `sparse_vec` 混合、纯 `dense_vec`、纯 `sparse_vec`。

### Filter

```bash
mfs search "login" postgres://prod --filter status=open
mfs search "login" postgres://prod --filter status=open,priority=high
mfs search "login" slack://eng --kind thread_aggregate
mfs search "login" --all --filter connector_type=postgres
```

filter 直接翻译成 Milvus `expr`，作用在 scalar field 或 JSON metadata 上。

### Collapse

默认按 chunk 召回，可能同一文件多个 chunk 都命中。`--collapse object` 让结果按 object_uri 去重，只保留每个 object 的 top score：

```bash
mfs search "session" ./src --top-k 5 --collapse object
```

## 8. Grep 流程

详细派发见 [05 §6](05-browse-and-read.md#6-grep-的派发)。这一节补充 Milvus 召回路径——它是 `mfs grep` 在**对象已索引、又没有 pushdown** 时的默认主路径（不是 `--mode` 开关触发的可选项）：

```
Milvus sparse search:
  filter: namespace_id IN current_namespaces AND connector_uri = Y AND object_uri = Z
  query : sparse vector from pattern
  返回带 chunk content 的 hits → chunk 片段 + locator / lines
```

- 优势：跨大文件 / 远端 connector / 异构 `--all` 查关键词不用线性扫，CS 下统一可用；BM25 索引是建 dense 时顺带就有的，零额外成本
- 取舍：召回的是 chunk 文本，BM25 是 token 统计相关、不是 regex，不保证精确字面匹配，返回粒度是 chunk 片段而非行级。要精确穷尽用 pushdown 源或 `mfs export` + 本地 grep（见 [05 §6](05-browse-and-read.md#6-grep-的派发)）

能 pushdown 的 connector（postgres / slack / s3）优先走 pushdown 拿精确结果；BM25 是兜住其余已索引对象的统一路径。

## 9. 跨 connector search 示例

```bash
$ mfs search "why did we change pricing limit" --all --top-k 5

[1] linear://product/teams/Pricing/issues.jsonl  score=0.882
    chunk_kind=record_aggregate  locator={"team":"Pricing","id":"LIN-88"}
    title: Lower enterprise pricing cap to $10k/month
    state: Done

[2] github://product/_meta/pulls.jsonl  score=0.821
    chunk_kind=record_aggregate  locator={"number":312}
    title: Update pricing rate limit config
    merged_at: 2026-04-22

[3] slack://eng/channels/pricing__C09/2026-04-22/threads.jsonl  score=0.794
    chunk_kind=thread_aggregate  locator={"channel":"pricing__C09","date":"2026-04-22","thread_ts":"1713780000.111"}
    thread: 12 messages, U1/U2/U3
    discussion of new pricing cap and rollout plan
```

Agent 可以同时拿到 Linear issue、GitHub PR、Slack thread 三类不同 connector 的结果，envelope 一样。

## 10. Embedding / Summary / VLM / Converter providers

四类外部加工工具走同一种插件化模型——都是 pipeline 里"产出 artifact 或 chunk"的可换组件，结果都过 cache（02 §10.4）。framework 全局配置放 server 端 `~/.mfs/server.toml`（本地 daemon）或 `/etc/mfs/server.toml`（远端部署）：

> **已知限制：v0.4 这四类配置全局单一，不能 per-connector**。整个 MFS 实例一个 embedding 模型、一个 converter、一个 VLM、一个 summary LLM，所有 connector 共用。
>
> 为什么不能 per-connector（尤其 embedding）——不是加个配置项的事：① 不同 embedding 模型**维度可能不同**，而 Milvus collection 维度建表时定死，一张大表装不下多种维度；② 不同模型 = **不同向量空间**，query 向量没法跨空间比，跨 connector 的 `search --all` 统一排序会失效。
>
> **v0.5+ 方向（config-profile）**：定义若干"处理配置组"，每组打包一套 converter + embedding + VLM + summary，并对应一张自己的 collection（**只有 embedding 模型决定 collection 切分**，其余配置不影响）。connector 只需归属一个 config-profile；搜索时按组的 embedding 查该组 collection，跨组各出一份结果（不强行跨空间合并）。注意它跟 client 端 profile 无关，文档里别用裸 "profile" 称呼。
>
> **v0.5+ collection 全景**：collection 切分不是"几选一的 enum"，是**两个独立的轴**——
>
> ```
> 轴① namespace（隔离：谁能访问）        可选（合规才开）
> 轴② config-profile（向量空间：怎么 embed）多 embedding 模型时强制开（维度不同必须拆表）
> partition_key = connector_uri          永远，表内按 connector 分桶（软边界）
>
> collection 名 = 两轴组合:
>   都不分           → mfs_chunks                 （v0.4 默认）
>   只分 namespace   → mfs_chunks__<ns>           （v0.4 的 per_namespace）
>   只分 profile     → mfs_chunks__<profile>      （单租户多 embedding）
>   两个都分         → mfs_chunks__<ns>__<profile>
> ```
>
> 每张 collection 内部 schema / partition_key / chunk_id 公式完全一致，唯一不同是 `dense_vec` 维度（跟着该 profile 的 embedding 模型）。config-profile 是被砍掉的 `per_connector` 的"分组修复版"（按组拆而非逐 connector 拆，不爆炸），落在 collection 硬边界这一层。整套 slot 进 [02 §9.4](02-architecture.md#94-milvus-隔离collection_strategy) 的 `collection_strategy`。

```toml
[embedding]
provider = "openai"             # openai | onnx | google | voyage | jina | mistral | ollama | local
model    = "text-embedding-3-small"
batch_size = 100

[summary]
enabled  = "auto"
provider = "openai"
model    = "gpt-4o-mini"
max_tokens = 800
min_size_kb = 8                 # auto 时阈值

[vlm]
provider = "openai"
model    = "gpt-4o-mini"        # 必须是 vision 模型
prompt   = "Describe this image..."

[converter]
default = "markitdown"          # 默认：一个库吃 PDF/DOCX/DOC/PPT/XLSX/图片/HTML，轻
                                # 高质量可选 backend：docling | marker | mineru | llamaparse（重，按需路由）
                                # 按文件类型 / path 自动路由

# 按 path glob 路由到特定 converter（可选；markitdown 对复杂 PDF 偏弱时上重型）
[[converter.routes]]
match = "**/scanned/*.pdf"
provider = "docling"            # 扫描件 / 复杂表格 / 布局：vision 模型质量显著更好

[[converter.routes]]
match = "**/papers/*.pdf"
provider = "marker"             # 学术 PDF
```

切换 embedding model 后用 `--force-index` 重建：embed 的 cache key 含 model+version，旧模型全 miss → 重新 embed；chunk 文本不变所以 convert 命中、不重转。Milvus 不支持列级 update，所以是 DELETE by object_uri + 整行 re-INSERT。批量 DELETE-by-filter + 批量 INSERT 比逐条 upsert 快很多。

切换 summary / vlm / converter 模型时，对应层的 transformation cache key（含 provider + model + version）跟着变 → 旧结果 miss → 自动重算，body chunk 不受影响：

- 换 summary / vlm → 只影响 `summary` / `directory_summary` / `schema_summary` / `vlm_description` 这几种 chunk_kind 的行，body chunk 不动
- 换 converter → convert 的 cache key 变（含 `converter + version`）→ miss → 重新转 markdown + 重新 chunk + 重 embed；用户的源文件不需要重传

这套是 [04 §5.2](04-connector-and-ingest.md#52-重建与-cache) cache 模型的直接应用——每个操作的 cache key 都把所属 provider / model / version 揉进去，换工具 → key 变 → 自动重算对应层，不靠多层 fingerprint chain。

**converter 路线图**：v0.4 默认 `markitdown`（一个库覆盖 PDF/DOCX/DOC/PPT/XLSX/图片/HTML），`docling / marker / mineru / llamaparse` 等高质量 converter 作为可选 backend（`mfs-server[converter-docling]` 等 extra 按需装）——它们对复杂表格、嵌入公式、扫描件显著更好但更重，不进默认安装，用户按文件类型 / path 路由即可。convert 结果进 transformation cache（key 含 `converter + version`，见 [02 §10.4](02-architecture.md#104-cache-层)），换 converter 自动失效重转。

> **HTML 有两条转换路径，别混**。framework converter 处理的是"connector 吐出**独立文件字节**"的场景——PDF / DOCX / 本地 `.html` 文件 / gdrive 导出的 HTML 等；上面 `[converter]` 的 HTML 能力就是给这类**文件态 HTML**。**web crawler 不走这条**：web connector 的 HTML→markdown 跟抓取 backend 耦合（`static` 内联 markitdown，`crawl4ai` 自带 JS 渲染 + 正文抽取），在 connector 内部完成，结果只进 `converted_md` artifact cache、靠 ETag 304 省重抓，**不进 transformation cache**。分界线是"转换是否跟抓取 backend 耦合"——耦合的内联（web），不耦合的（吐独立文件）走 framework converter。详见 [07 §6.2](07-contributing-connector.md#62-web-connector动态发现-path-tree)。

### 10.1 Transformation cache：跨调用复用 API 结果

convert / embedding / vlm / summary 都是 **纯函数 + 贵**——同一段输入经过同一模型/工具必然产出同一结果。framework 在这四类 client 外面包一层 **content-addressable transformation cache**，跨对象 / 跨连接器 / 跨 namespace 复用结果。

```
worker → CachingEmbeddingClient.batch_embed(texts)
            │
            ├── cache.batch_get(keys)
            │     hit → 复用 vector，零 API
            │     miss ↓
            ├── BatchingEmbeddingClient.embed_many(miss_texts)
            │     → 真 API call
            └── cache.enqueue_put(...)  (异步写回，不阻塞主流程)
```

命中场景：

- 同一段 boilerplate / 文档段落 出现在多个文件 → 只 embed 一次
- Slack 里有人贴了 GitHub issue 的内容 → 只 embed 一次
- Milvus collection drop 重建 → cache 还在，零 API 重 embed
- embedding model 回滚 v2 → v1 → cache 里 v1 vector 还在

完整 schema / client 包装层 / 异步 writer / LRU eviction / observability 详见 [02 §10.4](02-architecture.md#104-cache-层)。

**Converter（PDF→md / DOCX→md）v0.4 也进 transformation cache**（key = `sha1(原文件 bytes + converter + version)`）——收敛掉 fingerprint chain 后，配置变化靠 `--force-index` 重跑整条 pipeline，convert 进 cache 才能让重建时"原文件没变就命中、零转换成本"。它跟 artifact cache 的分工：artifact cache 按 object_uri 存"这个对象的 converted_md"给 `cat`/chunker 快速读；transformation cache 按内容存"这段 bytes 用这个 converter 转出来的结果"，跨对象/跨重建复用。converted_md 的字节物理上存哪（object store 还是 cache）实现时择一，对外都是"命中"。

**两层 cache 关系速查**：

| 名字 | 寻址 | 谁用 | 文档 |
|---|---|---|---|
| **artifact cache** | `object_uri + artifact_kind` | `cat / head / chunker` 读派生产物 | [02 §10.2](02-architecture.md#102-object-store) / [05 §10](05-browse-and-read.md#10-artifact-cache-层细节) |
| **transformation cache** | `sha1(input) + kind + provider + model + version + config` | convert / embedder / vlm / summary 跳过重复 API | [02 §10.4](02-architecture.md#104-cache-层) |

两层物理上分文件、职责完全独立，不会互相干扰。

## 11. 大对象索引控制

千万行的表如果默认 `chunk_strategy=per_row`，一次 `mfs add` 会写出千万 chunk + 调千万次 embedding。控制手段：

### 11.1 `chunk_max` 硬上限（framework 内置保守默认）

framework 内置默认 `chunk_max = 1_000_000`（一百万 chunk / object），server.toml 可以全局覆盖，单个 `[[objects]]` 段可以再覆盖：

```toml
# server.toml（全局默认；如果不写就走 framework 内置 1_000_000）
[chunk]
default_chunk_max = 1000000

# connector TOML
[[objects]]
match = "public.events"
text_fields = ["event_type", "payload_summary"]
chunk_max = 100000                # 这个对象单独压低
```

达到上限停止生成（已生成的 chunk 保留 = 部分索引，`search_status=partial`）+ 报错：

```text
chunk_max_exceeded: public.events
This object would produce ~12.4M chunks, exceeding chunk_max=1000000 (framework default).
Add index_filter or chunk_strategy to limit, or explicitly raise chunk_max:

  [[objects]]
  match = "public.events"
  index_filter = "updated_at > '2026-04-01'"
  # or
  chunk_strategy = "windowed"
  chunk_window = "30d"
  # or
  chunk_max = 20000000            # 显式承诺承担成本
```

为什么默认是 1M 而不是无穷：embedding API 调用花钱、Milvus 写入慢，默认需要一道防线挡住"用户 `--yes` 跳过 estimate 直接跑 5000 万行表"的事故。1M 是个保守值——大部分团队的 ticket / issue / message 量都在 100k 量级；真有大需求要显式提一行配置，迫使用户做一次决定。

### 11.2 windowed 策略

```toml
[[objects]]
match = "public.events"
chunk_strategy = "windowed"
chunk_window = "30d"            # 只索引最近 30 天（按 updated_at）
```

### 11.3 sampled 策略

```toml
[[objects]]
match = "public.audit_log"
chunk_strategy = "sampled"
sample_rate = 0.01              # 1% 抽样
```

### 11.4 estimate + 确认（首次 add）

```text
$ mfs add postgres://prod
Probing connector and sampling records (local tokenizer only, no embedding API)...

Estimated (±50% accuracy):
  scan:      12.4M rows across 38 tables
  chunks:    ~14M    (chunker dry-run on sample)
  tokens:    ~2.5B   (apply your provider's per-token rate to estimate $)

Continue? [y/N]
  Or limit scope:
    mfs add postgres://prod --tables-only public.tickets,public.accounts
    mfs add postgres://prod --schema-only
```

估算流程：

1. 探测 connector 暴露的对象总数和 size_hint（不读对象内容）
2. 抽样小批量 record（默认 `min(1000, 1%)`）跑 **chunker + 本地 tokenizer**——chunker 确定性、tokenizer 本地，零外部成本，不调 embedding，不写 Milvus
3. 按抽样外推总 chunks / tokens，明示 ±50% 精度
4. 用户决定继续 / 限定范围 / 取消

**估算阶段零计费**：用户看到 prompt 时还没花一分钱。**只给物理量，不给钱、时间、storage**：embedding provider 价格不同（OpenAI / Voyage / Cohere / 自部署 / 企业协议），时间受 worker 并发 / rate limit / 网络浮动 10x，storage 强依赖 embedding dim。token 数靠抽样 tokenizer 算出来是可靠的"工作量"指标，用户拿着自己 provider 的 rate 算钱。实际进度上线后看 `mfs status`。

> 估算给的是**全集**总量；单个 object 仍受 `chunk_max`（§11.1，默认 1M/object）约束。若估算显示某个对象会超 `chunk_max`，prompt 里会标出来——继续跑时该对象会 `chunk_max_exceeded` 部分索引（不是整个 add 失败），用户要么提前加 `index_filter` / `windowed`，要么显式抬高 `chunk_max`。所以"总 chunks 估算"和"逐对象 chunk_max"是两件事，别用前者推断每个对象都会被完整索引。

## 12. 删除与一致性

ingest 时如果对象消失：

```
Milvus DELETE WHERE namespace_id = X
                AND connector_uri = Y
                AND object_uri NOT IN (current_object_set)
```

ingest 时如果对象内 record 消失（per_row 模式）：

```
Milvus DELETE WHERE namespace_id = X
                AND connector_uri = Y
                AND object_uri = Z
                AND locator->>'id' NOT IN (current_pk_set)
```

per_row 模式下用 locator 做 record-level 删除。per_group 模式（slack thread）只能用粗粒度（删整 group 重新写）。

## 13. 什么时候不进 Milvus

不是所有 object 都需要走 chunk + embedding → Milvus 这条路。两种情况：

### 13.1 默认就不索引的 object_kind

[§6](#6-各-object_kind-的-chunk-来源) 列了哪些 object_kind 默认不进 Milvus：

| object_kind | 默认行为 | 仍能做什么 |
|---|---|---|
| `text_blob` (json / csv / log 真实文件) | 不进 Milvus | `cat / head / tail / grep` 兜底（grep 走线性扫或 connector pushdown） |
| `binary` | 不进 Milvus（metadata only） | `cat --meta` 看元数据，`cat --raw` 看原字节 |
| `directory` | 不进 Milvus | `ls / tree` 看结构，开启 `directory_summary` chunk_kind 才进 Milvus |

这些靠 `object_kind_of(path)` 判定，**不需要用户配置**。

### 13.2 显式不索引：`indexable = false`

某些 object 默认 object_kind 会进 Milvus，但业务上不想索引（敏感数据 / 量太大 / 没语义搜需求）——用 `indexable = false` 显式跳过：

```toml
[[objects]]
match = "public.audit_*"
indexable = false        # 整张表都不进 Milvus

[[objects]]
match = "internal.users"
indexable = false        # 用户表 grep 找用户名足够，不需要语义搜
```

效果：

- 不进 Milvus（零 embedding API 调用 + 零 Milvus storage）
- 仍能 `cat / head / tail / grep`——grep 走 connector pushdown 或线性扫
- 不能 `mfs search`——`mfs status` 显示该 object `not indexed (indexable=false)`

### 13.3 典型场景

| 场景 | 该用什么 |
|---|---|
| 巨量 audit log / event log | `indexable=false` 或 `chunk_strategy=sampled` |
| 用户 / 用户名 / 邮箱列表 | `indexable=false`，靠 grep |
| 二进制 / 媒体（不开 VLM 时） | 默认就不进，无需配 |
| 数据敏感不想嵌入向量 | `indexable=false` |
| 表太大、chunk_max 兜不住 | `indexable=false` 或加 `index_filter` 缩范围 |

`mfs ls --json` 的 `capabilities.indexable` 字段会暴露给 agent 看，避免 agent 试 search 无果。

### 13.4 跟 search availability 的关系

下面 §14 `mfs status` 输出里的 `unavailable` 状态正是"无任何 chunks"的标识——包括"未配 text_fields"和"全部 indexable=false"两种成因。

## 14. 搜索可用性 (search availability)

`mfs status` 输出包含每个 connector 的 search 状态：

| 状态 | 含义 |
|---|---|
| `available` | 该 connector 至少一个对象有 chunks 在 Milvus |
| `partial` | 部分对象有 chunks，部分未建 |
| `building` | 正在构建中 |
| `unavailable` | 无任何 chunks（未配 text_fields，或全 indexable=false） |

```text
$ mfs status postgres://prod
Connector: postgres://prod
Health: ok
Sync:   last 2026-05-15T07:00, status=fresh
Index:
  tables/public/tickets/rows.jsonl   12453 chunks (fresh)
  tables/public/accounts/rows.jsonl  890 chunks (fresh)
  tables/public/events/rows.jsonl    partial (chunk_max_exceeded)
  tables/public/audit_log/rows.jsonl not indexed (indexable=false)
Search: partial
```

## 15. JSON envelope (search/grep)

```json
{
  "source": "postgres://prod/public/tickets/rows.jsonl",
  "lines": null,
  "content": "subject: Login broken after SSO migration\n\ndescription: Enterprise users cannot complete SSO redirect.",
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
      "priority": "high",
      "assignee": "alice",
      "updated_at": "2026-05-10T12:30:00Z"
    }
  }
}
```

### 统一外壳 + per-connector 内核

envelope 的设计原则是**外壳固定、内核可变、可变的部分也被文档化**：

- **固定外壳**（每个 connector 都填同样的顶层 key）：`source / lines / locator / content / score / metadata{kind, chunk_kind, connector_type, media_type, fields}`。agent 只认这层，跨 connector 一致
- **可变内核**：`locator` 的内部结构（postgres `{schema,table,pk}` / slack `{channel,date,thread_ts}`）和 `metadata.fields` 的字段（ticket 的 status/priority、PR 的 merged_at）per-connector。但每个 connector 的 locator schema 是**文档化的稳定契约**（见 [§3](#3-locator-schema-per-connector) 的表 + skill 里该 connector 的 reference 文档），不是自由发挥

agent 读外壳，遇到 locator 内核就查该 connector 文档化的 schema——**差异是"被文档化的差异"**。

### lines 与 locator：独立、不互斥、有优先级

两个都是可选字段，谁有意义填谁，**可以同时非空**：

- `lines = [start, end]`：chunk 在某个文本对象里的行区间，用于 `cat <source> -n A:B` 导航
- `locator`：容器内单元的 key 定位（row pk / thread_ts / issue number）

agent 用的优先级：**locator 非空就优先用 locator**（精确单元）；只有 lines 非空才用行区间。每种 chunk_kind 填哪些是稳定契约：

| chunk_kind | lines | locator | 重开方式 |
|---|---|---|---|
| `body`（document/code）| ✓ | null | `cat --range start:end` |
| `row_text`（DB row / record）| null | ✓ pk | `cat --locator` by pk |
| `thread_aggregate`（slack/discord/gmail）| 可有（在 messages.jsonl 的行）| ✓ thread_ts | **优先 locator**，lines 仅辅助 |
| `record_aggregate`（issue+comments）| 可有 | ✓ number | **优先 locator** |
| `vlm_description`（图片）| null | null（用 source 本身）| `cat <source> --meta` |
| `summary` / `directory_summary` / `schema_summary` | 看来源对象 | 看来源对象 | 跟随其来源 object |

`thread_aggregate` / `record_aggregate` 这种两者都有的，**locator 是权威重开 key，lines 只是定位它在 jsonl 文件里大致位置的辅助**。
