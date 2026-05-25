# 贡献新 Connector

这一篇写给想给 MFS 加新 connector 的开发者。预期工作量：500~1500 行 Python，集中在 `connectors/<name>/`，按实现到哪一层而定。

## 开始之前：用大白话先讲一遍你在干什么

下面这一节不含任何契约细节，只帮你建立直觉。看懂了再往下读 §0 起的精确接口。

### 一句话

MFS framework 是**一台已经造好的机器**：从上游把数据拉过来之后的所有重活——切 chunk、调 embedding、写 Milvus、混合检索、两层缓存、存储、HTTP API，还有 `ls / tree / cat / head / tail / grep / search / export` 这些命令的实现——**全都做好了**。你写一个 connector，只是给这台机器接上"**怎么够到你这个数据源**"的那几根线。其余的你**白拿**。

### 你的代码在整个数据流的哪一段

```
上游数据源（postgres / 某网站 / slack / ...）
   │
   │   ← 只有这一段是你写的：连上游 + 把它"长成"一棵虚拟文件树
   ▼
┌──────────── 你的 connector（~500–1500 行）─────────────┐
│  stat / list / read(_records) / fingerprint /         │
│  sync / object_kind_of      （6 个核心方法）          │
└───────────────────────┬────────────────────────────────┘
   │   目录项 / 字节 / record / ObjectChange
   ▼
╔═══════════ 以下全是 framework，你碰不到、也不用写 ═══════════╗
║  任务队列 → chunk 切分 → embedding / VLM / summary →       ║
║  写 Milvus → cache 两层 → deletion reconcile               ║
║                                                            ║
║  HTTP /v1 → ls / tree / cat / head / tail / grep /         ║
║  search / export 的命令实现 + 输出渲染 + 大对象 guard +    ║
║  分页 + 错误码 + 多租户 namespace                          ║
╚════════════════════════════════════════════════════════════╝
   │
   ▼
agent / 用户 敲 mfs 命令
```

### 关键：命令是"白拿"的——你实现底层方法，命令自动就有了

很多人卡在"框架有个 `tree` 命令，可贡献接口里没看到 `tree`，那它从哪来？"——**你不实现 `tree`，`tree` 也能用**，因为框架的 `tree` 就是去**递归调你的 `list`**。同理一排命令都是这么"派生"出来的：

| 用户敲的命令 | framework 自动做的 | 你只需提供 |
|---|---|---|
| `mfs ls <uri>` | 取孩子、排序、单层截断 100、metadata DB 缓存、渲染表格 | `list(path)` 返回直接子节点 |
| `mfs tree <uri> -L 2` | **递归**调你的 `list`、控制深度 `-L`、时间倒序、截断、画成树 | 还是那个 `list`（你没写 tree，tree 自动有） |
| `mfs cat / head / tail / export` | 大对象 guard、`--range` 解析、按 media_type 渲染、artifact cache | `read`（字节）或 `read_records`（record 流）+ `stat`（给 size） |
| `mfs grep "..."` | 已索引对象走 Milvus BM25 召回；未索引才线性扫你的 `read` 流、`-C/-i/-w`、限速截断 | 啥都不用（想更快 / 要精确穷尽才重写 `grep` 做下推） |
| `mfs search "..."` | 切 chunk、embed、写 Milvus、混合召回、RRF、组 envelope | **几乎不写代码**：TOML 配 `text_fields` + `object_kind_of` 标对类型 |
| `mfs add` / 再同步 | 起 job、排队、调 chunk/embed、deletion、cache | `fingerprint`（算变化标记）+ `sync`（yield 变了的 object） |

一句话：**你填的是"数据怎么来"，framework 补的是"数据来了之后怎么用"。** 6 个核心方法写完，上面一整排命令就都能跑——不是你一条条命令去实现的。

### 那 6 个"洞"为什么偏偏是这 6 个

每个方法回答框架的一个问题，框架拿到答案就能驱动对应的命令/流程：

| 方法 | 它回答框架的问题 | 撑起什么 |
|---|---|---|
| `stat(path)` | 这个 path 是文件还是目录？多大？什么 media_type？变了没？ | cat 的大小 guard、ls 的每一行、freshness |
| `list(path)` | 这个目录下一层有哪些孩子？ | `ls` / `tree`（tree = 框架递归你的 list） |
| `read` / `read_records` | 把这个对象的内容给我（字节流 / record 流） | cat / head / tail / export / grep / chunk 的原料 |
| `fingerprint(path)` | 给我一个"变没变"的廉价标记（mtime / etag / version 单值） | 增量 sync 判断跳过谁、artifact 是否 stale |
| `sync(opts)` | 从上次到现在，哪些 object 变了？流式报给我 | `mfs add` 的增量、full scan 时的 deletion 推断 |
| `object_kind_of(path)` | 这个 path 该按哪类加工（document / code / table_rows / ...）？ | 框架据此选 chunker / 渲染器 / 是否进 Milvus |

> 直觉分两半：前三个（`stat` / `list` / `read`）是"**把数据源长成一棵能浏览、能读的虚拟文件树**"；后三个（`fingerprint` / `sync` / `object_kind_of`）是"**告诉框架什么变了、每个东西是什么**"。两半都给齐，剩下全自动。

### 哪些你完全不用碰（framework 全包）

chunk 怎么切、用哪个 embedding 模型、Milvus schema、检索 / RRF、两层 cache、deletion 逻辑、任务队列、HTTP API、命令外壳与输出渲染、错误码、多租户 namespace——**这些 framework 全做了，你想碰也碰不到**（[§11](#11-边界规则) 列了明确禁区，比如不准在 connector 里直接写 Milvus 或直接调 embedding API）。这条分割线是刻意画的：差异部分越薄，贡献一个新源越容易。

建立完这个直觉，下面 §0 起是精确版——先看"必须实现 vs 可选重写"，再到逐方法签名。

## 0. 必须实现 vs 可选重写

Connector 暴露两类方法：

```
必须实现（6 个核心方法）
  stat / list / read              核心 IO（read 与 read_records 二选一；纯字节实现 read，结构化实现 read_records）
  fingerprint / sync              变化检测
  object_kind_of                  路径 → object 类型映射

可选重写（基类有默认；重写就走你的逻辑）
  grep          默认派发：已索引走 Milvus BM25 / 未索引线性扫兜底；postgres / slack 可重写做下推（精确穷尽）
  search        默认 None（framework 走 Milvus 召回）；某些 connector 可用 provider search API
  chunk_plan    默认按 object_kind 推断；自定义 chunk strategy 时重写
  render        默认按 media_type 渲染；Parquet / ORC 等特殊格式可重写
  task_priority 默认 0（FIFO）；有"首屏可见"诉求的 connector（如 file）重写
```

只实现这 6 个核心方法就能跑起来（~500 行 Python）。需要性能或自定义能力时增量重写可选方法，每个独立、低耦合。

framework 不暴露更深的扩展点（自定义 chunker 内部、自定义 artifact cache 格式、直接写 Milvus 等）——这些层级 framework 接管，否则 framework 难维护，贡献者负担也重。

## 1. 你需要写什么（vs 不需要写什么）

| 关注点 | 你写 | 复用 framework |
|---|---|---|
| 连接外部系统 / 认证 | 用对应 SDK | 凭据通过 `credential_ref` 解析 |
| 决定 URI 树长什么样 | 写 `PROMPT.md` + `layout.py` | 命名规范见 §10 |
| `stat / list / read` 实现 | 三个 method | API 路由、HTTP、SSE 都是 framework |
| 变化检测（`fingerprint / sync`） | 算法 + state schema 自由 | framework 接管"哪些变化要重建" |
| 对象 → object_kind 映射 | 一个 dict | 每个 object_kind 的 chunker / structure 全 framework |
| 配置 schema 验证 | 用 pydantic | framework 调验证 |
| 内置 preset（可选） | 提供默认 text_fields / locator_fields | 没 preset 用户得显式配 |
| chunk 切分 / embedding / summary / VLM | | framework pipeline |
| `cat / head / tail / grep / ls / tree` | | framework shell helpers |
| Retrieval Index（Milvus） | | framework |
| metadata DB / artifact cache 存储 | | framework storage |
| HTTP API / SDK | | framework |
| `mfs add / connector / status` | | framework engine |

## 2. 文件骨架

```
connectors/<name>/
├── __init__.py
├── plugin.py            # ConnectorPlugin 子类；入口
├── config.py            # pydantic schema for connector TOML
├── connector.py         # 封装外部系统的 SDK / HTTP 调用，凭据持有
├── layout.py            # URI path ↔ 外部资源映射；object_kind_of()
├── sync.py              # change_set / fingerprint 实现
├── PROMPT.md            # 目录布局 ASCII 描述（给 agent skill）
├── presets.py           # 可选：内置 text_fields / metadata_fields preset
└── tests/
    ├── test_layout.py
    ├── test_sync.py
    ├── test_e2e.py
    └── fixtures/        # fake API 响应数据
```

## 3. ConnectorPlugin 契约

```python
# server/python/src/mfs_server/connectors/base.py

class ConnectorPlugin(ABC):

    # ─────── 元信息（class attribute）─────────────
    NAME: str                          # "postgres"
    URI_SCHEME: str                    # "postgres"
    DISPLAY_NAME: str                  # "Postgres"
    PROMPT: str                        # 目录布局描述，agent skill 直接拼用
    CAPABILITIES: "Capabilities"       # 见 §4
    CONFIG_SCHEMA: type[BaseModel]     # pydantic model 校验 TOML

    # ─────── 生命周期 ──────────────────────────────
    def __init__(self, config: BaseModel, credential: Any, *, ctx: ConnectorContext):
        """framework 对【每个已注册 connector】各实例化一个 plugin（一连接一实例），
        把该 connector 的 config / credential / ctx 注进来。所以 self.pool /
        self.session 这类运行时对象是 per-instance、不跨 connector 共享；
        ctx 里:
        - state:           持久化 KV store（落 connector_state 表，按 connector_id 隔离），
                           connector 自己定义 schema——详见下方 StateStore
        - connector_id / namespace_id
        - object_config_for(path) → ObjectConfig（从 [[objects]] TOML 段解析）
        """
        self.config = config
        self.credential = credential
        self.state = ctx.state           # 快捷别名
        self.ctx = ctx

    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def healthcheck(self) -> HealthStatus: ...

    # ─────── 必须实现：核心 IO（abstract method）───────────────
    @abstractmethod
    async def stat(self, path: str) -> PathStat: ...
    @abstractmethod
    async def list(self, path: str) -> list[Entry]: ...

    # read 跟 read_records 二选一：
    # - 纯字节 connector（file / s3 / web）只实现 read
    # - 结构化 connector（postgres / saas）只实现 read_records，framework 自动 wrap 出 read

    async def read(
        self, path: str, range: Range | None = None
    ) -> AsyncIterator[bytes]:
        """字节流，给 cat/head/tail/grep/export 用。
        如果只实现了 read_records，framework 自动按 jsonl 格式（每行一条 dict）
        把 records 序列化成 bytes 兜底，connector 不用再写一遍。"""
        # 默认实现：调 read_records 后 json.dumps + b"\n"
        # 注意 read_records 的契约：未实现时返回 None（基类的普通 def）；
        # 实现了就是 async generator，调用返回 async iterator。所以这里 is None 能区分两者。
        records = self.read_records(path, range)
        if records is None:
            raise NotImplementedError("either read or read_records must be implemented")
        async for r in records:
            yield (json.dumps(r, default=str) + "\n").encode()

    # 基类是普通 def 返回 None（不是 async def——否则调用永远返回 coroutine、is None 判不出）。
    # 结构化 connector override 成 async generator（async def + yield），见下方 Postgres 例子。
    def read_records(
        self, path: str, range: Range | None = None
    ) -> AsyncIterator[dict] | None:
        """结构化数据时 override 成 async generator，yield record dict 流，
        chunker 直接按 chunk_strategy（per_row / per_group）消费。
        纯字节 connector 不实现，保留基类返回 None。"""
        return None

    # ─────── 必须实现：变化检测 ───────────────────────
    @abstractmethod
    async def fingerprint(self, path: str) -> str | None:
        """返回该 path 的当前 upstream 变化标记（mtime+size / etag / version 之类，单值）。
        None 表示总是 fresh。framework 存起来、下次比对判断这个 object 变没变——
        只这一层，没有多层 fingerprint chain。变了就重跑整条 pipeline，
        中间产物（convert/embed/vlm/summary）的复用靠 content-addressable cache（04 §5.2）。"""

    @abstractmethod
    async def sync(self, opts: SyncOptions) -> AsyncIterator[ObjectChange]:
        """同步：流式 yield 每个变化的 object。
        - opts.full=True：用户加了 --force-index，跳过 cursor 走全量
        - opts.since: 用户传了 --since <date>，覆盖 state 里的 cursor
        cursor / manifest / etag / state schema 都在 connector 内部，
        通过 self.state（KV store）持久化，framework 不 introspect。"""

    # ─────── 必须实现：路径分类 ──────────────────────
    @abstractmethod
    def object_kind_of(self, path: str) -> ObjectKind:
        """把虚拟 path 映射到 object_kind。
        例：rows.jsonl → "table_rows"
            messages.jsonl → "message_stream"
            schema.json → "table_schema"
            真实 .md/.py/.png → 按扩展名"""

    # ─────── 可选重写：基类有默认实现 ──────────────────
    async def grep(
        self, pattern: str, path: str, options: GrepOptions
    ) -> AsyncIterator[GrepMatch] | None:
        """默认走 framework grep 派发（已索引对象走 Milvus BM25，未索引线性扫兜底）；postgres/slack 等可重写做下推。
        返回 None = 用 framework default。
        options.text_fields / metadata_fields 由 framework 从 ObjectConfig 注入。"""
        return None

    async def search(
        self, query: str, path: str, options: SearchOptions
    ) -> AsyncIterator[Hit] | None:
        """默认 None = framework 走 Milvus 召回；某些 connector 可重写用 provider search API。"""
        return None

    def chunk_plan(self, path: str) -> dict | None:
        """默认按 object_kind 推断；自定义 chunk strategy/preset 时重写。"""
        return None

    def render(self, path: str, media_type: str) -> str | None:
        """默认按 media_type 渲染（cat 输出）；Parquet/ORC 等可自定义。"""
        return None

    def task_priority(self, change: ObjectChange) -> int:
        """返回该 object_task 在队列里的优先级，越小越先处理。
        默认 0 (FIFO within the job)。只有有"首屏可见"诉求的 connector
        需要重写——例如 file connector 让 README / 配置 / src/ 先索引。
        Postgres / Slack / GitHub 一般保留默认即可。
        v0.4 由 connector 写死，不暴露给用户配置（见 02 §6.3）。"""
        return 0
```

> **术语**：方法签名里的 `path: str` 是 **connector root 内的相对路径**（如 `/public/tickets/rows.jsonl`），不是完整 URI。framework 调用前已经剥掉 URI 的 scheme + alias 前缀。用户面看到的 `<uri>`（如 `postgres://prod/public/tickets/rows.jsonl`）和 connector 方法收到的 `path` 是两个层级，详见 [02-architecture.md §2 术语](02-architecture.md#2-术语)。

`Capabilities`：

```python
@dataclass
class Capabilities:
    # sync
    manual_sync: bool = True            # v0.4 都是 True；定时调度不内置，用户自带 cron（见 04 §9）
    watch: bool = False                 # 仅 file connector true
    cursor_kind: str | None = None      # "updated_at" / "snowflake" / "etag" / None
    full_scan: bool = True

    # deletion detection 模式（决定 framework 怎么走 deletion reconcile，详见 02 §7.4）
    delete_detection: Literal[
        'never',          # 源头不能识别 delete（如 slack message）→ 永远跳过 deletion
        'explicit',       # 只在 yield "deleted" event 时删（最保守，默认）
        'full_scan',      # 本次是全量枚举 → framework 用全集 diff 推断 delete
                          #   （file 每次都全量；postgres 等增量 connector 在用户跑全量 sync 时才是 full，
                          #    connector 通过 SyncOptions 告诉 framework "本次是不是 full"）
        'state_change',   # 用 state 变化（closed/locked/archived）替代 delete
    ] = 'explicit'

    # object access（声明 connector 是否重写了对应方法、有更高效的实现）
    grep_pushdown: bool = False          # 重写了 grep()，做 SQL ILIKE / provider search / S3 Select
    search_pushdown: bool = False        # 重写了 search()，用 provider search API
    paged_cat: bool = True               # 支持 cat --range 区间读取
```

`mfs connector inspect <root>` 直接 dump 这个。

## 4. 数据结构

```python
@dataclass
class PathStat:
    path: str
    type: Literal["file", "dir"]
    media_type: str | None             # "application/x-ndjson" 等
    size_hint: int | None
    fingerprint: str | None
    extra: dict                        # connector-specific hint (row_count, etc.)

@dataclass
class Entry:
    name: str
    type: Literal["file", "dir"]
    media_type: str | None
    size_hint: int | None
    extra: dict

@dataclass
class Range:
    start: int
    end: int                            # 闭开 [start, end)；约定 -1 表示末尾

@dataclass
class ObjectChange:
    uri:     str
    kind:    Literal["added", "modified", "deleted", "renamed"]
    old_uri: Optional[str] = None    # 仅 renamed：原 path / URI
    # `renamed` 是可选 kind——connector 能可靠识别"同内容换 path"时主动 yield，
    # 让 framework 跳过重 chunk / 重 embed，只改 Milvus 的 chunk_id 主键。
    # 不能识别的 connector 照旧 yield added + deleted，正确但更贵。
    # 详见 [04 §5.7](04-connector-and-ingest.md#57-rename-detection)。

@dataclass
class SyncOptions:
    full:  bool = False                 # 用户 --force-index → True
    since: Optional[str] = None         # 用户 --since <date>，覆盖 state 里的 cursor

# self.state：framework 注入的、【持久化 + 按 connector 隔离】的命名空间 KV store
# 不是内存对象——背后是 metadata DB 的 connector_state 表（PK = connector_id + key），
# 进程/daemon 重启后还在；每个已注册 connector 各起一个 plugin 实例、各自一份 state，
# 所以同类型连多个数据源（postgres://prod 与 postgres://staging）的 state 互不干扰。
# 只放小状态（cursor / etag / token）；大的 path/object 全量映射别塞这里（见 §11）。
class StateStore(Protocol):
    async def get(self, key: str) -> Any | None: ...
    async def set(self, key: str, value: Any) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def checkpoint(self) -> None: ...    # 仅 cursor / monotonic-set state 安全调
    # value 可以是任意 JSON-serializable 结构（dict / list / str / number），
    # connector 自己定义 schema，framework 不 introspect。

# self.ctx：framework 注入的运行时上下文
@dataclass
class ConnectorContext:
    state:        StateStore
    connector_id: str
    namespace_id: str

    def object_config_for(self, path: str) -> ObjectConfig:
        """按 path 在 [[objects]] match glob 中找匹配项；
        没匹配的用默认配置（按 object_kind 推断）。grep / chunker 用。"""

    def declare_enumeration(self, mode: Literal["full", "incremental", "explicit_only"]) -> None:
        """connector 在 sync() 里声明【本次实际枚举模式】，framework 据此决定能否做全集 diff 删除。
        不调 = 默认 incremental（最保守，跳过 deletion）。只有真完整枚举了全集才声明 full——
        中途 raise 没声明到就按未完整处理、不删。详见 02 §7.4。"""

@dataclass
class ObjectConfig:
    """从 connector TOML 的 [[objects]] 段解析而来。framework 注入。"""
    text_fields:     list[str]
    metadata_fields: list[str]
    locator_fields:  list[str]
    chunk_strategy:  str            # per_row / per_group / per_field_chunked / windowed / sampled
    indexable:       bool = True
    chunk_max:       int = 1_000_000
    index_filter:    Optional[str] = None
    text_template:   Optional[str] = None
    group_by:        Optional[str] = None
    session_idle_min: Optional[int] = None
    chunk_window:    Optional[str] = None        # chunk_strategy=windowed，如 "30d"
    sample_rate:     Optional[float] = None      # chunk_strategy=sampled，如 0.01
    max_text_chars:  Optional[int] = None        # 单 chunk 上限，超过自动转 per_field_chunked

@dataclass
class GrepMatch:
    path:           str
    locator:        Optional[dict] = None      # 结构化 connector 用（postgres row 的 pk）
    line_no:        Optional[int] = None       # 文本 connector 用
    content:        str = ""                   # 匹配到的内容片段
    context_before: list[str] = field(default_factory=list)
    context_after:  list[str] = field(default_factory=list)

@dataclass
class GrepOptions:
    pattern:          str
    case_insensitive: bool = False
    context_lines:    int = 0
    # framework 从 ObjectConfig 注入：
    text_fields:      list[str] = field(default_factory=list)
    metadata_fields:  list[str] = field(default_factory=list)

@dataclass
class HealthStatus:
    ok: bool
    detail: str
    extra: dict                         # connection latency, permissions, ...
```

## 5. PROMPT.md 范本

每个 connector 写一段 ASCII，描述自己 root 下的目录布局。**发版时 CI 会把它自动收进 agent skill 的 `references/connectors/<name>.md`**（详见 [08 §6](08-agent-skill.md#6-skill-目录结构)）——贡献者只管写好这份 PROMPT.md，不用手动发布。

```
{prefix}                                          # = connector root URI 例如 postgres://prod

  database.json                                   # cross-schema 概览
  <schema>/
    tables/<table>/
      schema.json                                 # column / PK / FK / index
      rows.jsonl                                  # 全部行（lazy，大表不物化）
    views/<view>/
      schema.json
      rows.jsonl

Hints:
  - Read database.json first to understand schema layout.
  - rows.jsonl is large; cat refuses without --range.
    use head/tail/grep (which push down to SQL) instead.
  - search runs against row_text chunks built from configured text_fields.
```

`{prefix}` 是占位符，运行时 framework 替换成具体 connector root URI。

## 6. 两个真实例子

两个简化版 connector 展示完整接口怎么用——一个结构化（Postgres），一个动态发现 path 的（Web crawl）。生产 connector 完整代码在 `connectors/<name>/`，这里展示骨架到能跑的程度。

### 6.1 Postgres connector（结构化 connector 模板）

**`connectors/postgres/layout.py`** — 把虚拟 path 翻译成结构化节点（每个 connector 必备的工具）：

```python
from enum import Enum
from dataclasses import dataclass

class PgKind(str, Enum):
    ROOT          = "root"
    DATABASE_JSON = "database_json"
    SCHEMA_DIR    = "schema_dir"
    TABLES_DIR    = "tables_dir"
    TABLE_DIR     = "table_dir"
    TABLE_SCHEMA  = "table_schema"
    TABLE_ROWS    = "table_rows"

@dataclass
class PgNode:
    kind:   PgKind
    schema: str | None = None
    table:  str | None = None

def resolve(path: str) -> PgNode:
    parts = [p for p in path.strip("/").split("/") if p]
    if not parts: return PgNode(PgKind.ROOT)
    if parts == ["database.json"]: return PgNode(PgKind.DATABASE_JSON)
    if len(parts) == 1: return PgNode(PgKind.SCHEMA_DIR, schema=parts[0])
    if len(parts) == 2 and parts[1] == "tables":
        return PgNode(PgKind.TABLES_DIR, schema=parts[0])
    if len(parts) == 3 and parts[1] == "tables":
        return PgNode(PgKind.TABLE_DIR, schema=parts[0], table=parts[2])
    if len(parts) == 4 and parts[1] == "tables":
        if parts[3] == "schema.json":
            return PgNode(PgKind.TABLE_SCHEMA, schema=parts[0], table=parts[2])
        if parts[3] == "rows.jsonl":
            return PgNode(PgKind.TABLE_ROWS, schema=parts[0], table=parts[2])
    raise FileNotFoundError(path)
```

**`connectors/postgres/config.py`**：

```python
from pydantic import BaseModel
from typing import List

class PostgresConfig(BaseModel):
    schemas: List[str] = ["public"]
    max_read_rows: int = 10000
```

**`connectors/postgres/plugin.py`**：

```python
import json, asyncpg
from mfs_server.connectors.base import (
    ConnectorPlugin, Capabilities, PathStat, Entry, ObjectChange,
    HealthStatus, GrepMatch,
)
from .config import PostgresConfig
from .layout import PgKind, resolve

class PostgresPlugin(ConnectorPlugin):
    NAME = "postgres"
    URI_SCHEME = "postgres"
    DISPLAY_NAME = "Postgres"
    PROMPT = open(__file__.replace("plugin.py", "PROMPT.md")).read()
    CONFIG_SCHEMA = PostgresConfig
    CAPABILITIES = Capabilities(
        cursor_kind="updated_at",
        grep_pushdown=True,
        paged_cat=True,
    )

    # __init__ 用基类默认实现（config, credential, ctx）
    async def connect(self):
        self.pool = await asyncpg.create_pool(self.credential)

    async def close(self):
        await self.pool.close()

    async def healthcheck(self):
        try:
            async with self.pool.acquire() as c:
                await c.fetchval("SELECT 1")
            return HealthStatus(ok=True, detail="", extra={})
        except Exception as e:
            return HealthStatus(ok=False, detail=str(e), extra={})

    # ─── stat / list ───
    async def stat(self, path):
        node = resolve(path)
        if node.kind in (PgKind.ROOT, PgKind.SCHEMA_DIR,
                         PgKind.TABLES_DIR, PgKind.TABLE_DIR):
            return PathStat(path=path, type="dir",
                            media_type=None, size_hint=None,
                            fingerprint=None, extra={})
        if node.kind == PgKind.TABLE_SCHEMA:
            fp = await self._schema_fp(node.schema, node.table)
            return PathStat(path=path, type="file",
                            media_type="application/json",
                            size_hint=None, fingerprint=fp, extra={})
        if node.kind == PgKind.TABLE_ROWS:
            cnt, max_ts = await self._table_stats(node.schema, node.table)
            return PathStat(path=path, type="file",
                            media_type="application/x-ndjson",
                            size_hint=cnt * 200,
                            fingerprint=f"{max_ts}:{cnt}",
                            extra={"row_count_hint": cnt, "lazy": True})

    async def list(self, path):
        node = resolve(path)
        if node.kind == PgKind.ROOT:
            return ([Entry("database.json", "file", "application/json", None, {})]
                  + [Entry(s, "dir", None, None, {}) for s in self.config.schemas])
        if node.kind == PgKind.SCHEMA_DIR:
            return [Entry("tables", "dir", None, None, {}),
                    Entry("views",  "dir", None, None, {})]
        if node.kind == PgKind.TABLES_DIR:
            tables = await self._list_tables(node.schema)
            return [Entry(t, "dir", None, None, {}) for t in tables]
        if node.kind == PgKind.TABLE_DIR:
            return [Entry("schema.json", "file", "application/json", None, {}),
                    Entry("rows.jsonl",  "file", "application/x-ndjson",
                          None, {"lazy": True})]
        raise NotADirectoryError(path)

    # ─── read_records（chunker 直接用 dict 流；read 由基类自动 wrap） ───
    async def read_records(self, path, range=None):
        node = resolve(path)
        if node.kind == PgKind.TABLE_SCHEMA:
            yield await self._schema_json(node.schema, node.table)
            return
        if node.kind != PgKind.TABLE_ROWS:
            return None
        offset = range.start if range else 0
        limit  = (range.end - range.start) if range else self.config.max_read_rows
        async with self.pool.acquire() as c:
            async for r in c.cursor(
                f'SELECT * FROM "{node.schema}"."{node.table}" '
                f'ORDER BY {self._pk_cols(node.table)} '
                f'LIMIT {limit} OFFSET {offset}'
            ):
                yield dict(r)

    # ─── fingerprint / sync ───
    async def fingerprint(self, path):
        return (await self.stat(path)).fingerprint

    async def sync(self, opts):
        cursors = {} if opts.full else (await self.state.get("cursors") or {})
        if opts.since:
            cursors = {k: opts.since for k in cursors}
        # 声明本次枚举模式：全量 add → full（可推断删除）；增量 → incremental
        self.ctx.declare_enumeration("full" if opts.full else "incremental")

        for schema in self.config.schemas:
            for table in await self._list_tables(schema):
                yield ObjectChange(f"/{schema}/tables/{table}/schema.json", "modified")
                rows_uri = f"/{schema}/tables/{table}/rows.jsonl"
                last_ts = cursors.get(f"{schema}.{table}")
                if opts.full or await self._has_changes(schema, table, last_ts):
                    yield ObjectChange(rows_uri, "modified")
                    cursors[f"{schema}.{table}"] = await self._max_updated_at(schema, table)
                    await self.state.set("cursors", cursors)
                    await self.state.checkpoint()    # cursor 型 state，安全

    # ─── grep 下推 ───
    async def grep(self, pattern, path, options):
        node = resolve(path)
        if node.kind != PgKind.TABLE_ROWS:
            return None     # 用 framework 默认派发（BM25 / 线性扫兜底）
        text_fields = options.text_fields    # framework 从 ObjectConfig 注入
        if not text_fields:
            return None
        where = " OR ".join(f'"{c}"::text ILIKE $1' for c in text_fields)
        cfg = self.ctx.object_config_for(path)
        pk_cols = cfg.locator_fields
        async def gen():
            async with self.pool.acquire() as c:
                async for r in c.cursor(
                    f'SELECT * FROM "{node.schema}"."{node.table}" '
                    f'WHERE {where} LIMIT 1000', f"%{pattern}%"):
                    yield GrepMatch(
                        path=path,
                        locator={c: r[c] for c in pk_cols},
                        content=json.dumps(dict(r), default=str),
                    )
        return gen()

    def object_kind_of(self, path):
        return {
            PgKind.DATABASE_JSON: "document",
            PgKind.TABLE_SCHEMA:  "table_schema",
            PgKind.TABLE_ROWS:    "table_rows",
        }.get(resolve(path).kind, "directory")

    # ─── helper（实际还有更多，省略）───
    async def _list_tables(self, schema):
        async with self.pool.acquire() as c:
            return [r['table_name'] for r in await c.fetch(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = $1", schema)]
    # _table_stats / _max_updated_at / _has_changes / _schema_fp /
    # _schema_json / _pk_cols 略
```

注意几个用到新接口的地方：

- 没写 `__init__`，基类已经接 `ctx` 注入了 `self.state` / `self.ctx`
- `sync(self, opts)` 用 `opts.full` / `opts.since` 而不是猜
- `read_records` 直接 yield dict，**没写 `read`**——基类自动把 dict 序列化成 jsonl bytes 兜底
- `grep` 拿 `options.text_fields`（framework 从 connector TOML 的 `[[objects]]` 解析后注入）
- `GrepMatch` 用 `locator` 而不是不存在的"行号"
- `self.state.checkpoint()` 是 cursor 型 state，安全调

### 6.2 Web connector（动态发现 path tree）

**`connectors/web/plugin.py`** 骨架：

```python
import aiohttp
from markitdown import MarkItDown          # HTML→markdown，与 PDF/DOCX 等共用一个 converter
from mfs_server.connectors.base import (
    ConnectorPlugin, Capabilities, PathStat, Entry, ObjectChange,
)

class WebPlugin(ConnectorPlugin):
    NAME = "web"
    URI_SCHEME = "web"
    PROMPT = open(__file__.replace("plugin.py", "PROMPT.md")).read()
    CONFIG_SCHEMA = WebConfig
    CAPABILITIES = Capabilities(grep_pushdown=False, paged_cat=False)

    async def connect(self):
        self.session = aiohttp.ClientSession()
    async def close(self):
        await self.session.close()

    # ─── stat / list 都查 state 里的虚拟 tree ───
    async def stat(self, path):
        if path == "/" or self._is_intermediate_dir(path):
            return PathStat(path=path, type="dir", media_type=None,
                            size_hint=None, fingerprint=None, extra={})
        pages = await self.state.get("pages") or {}     # {path: {etag, size, ...}}
        if path in pages:
            p = pages[path]
            return PathStat(path=path, type="file",
                            media_type="text/markdown",
                            size_hint=p["size"], fingerprint=p["etag"],
                            extra={"url": p["url"]})
        raise FileNotFoundError(path)

    async def list(self, path):
        pages = await self.state.get("pages") or {}
        # 列出 path 下一级的孩子（path tree 自维护）
        children = self._children_of(pages, path)
        return [Entry(name=c, type=t, media_type=mt, size_hint=None, extra={})
                for c, t, mt in children]

    # ─── 纯字节 read，不实现 read_records ───
    async def read(self, path, range=None):
        # 现拉现转，不在 connector 里缓存正文——framework 会把这次输出写进
        # converted_md artifact cache（见 05 §5），再 cat 同一页直接命中、不重抓。
        pages = await self.state.get("pages") or {}
        md = await self._fetch_and_convert(pages[path]["url"])
        yield md.encode()

    async def fingerprint(self, path):
        pages = await self.state.get("pages") or {}
        return pages.get(path, {}).get("etag")

    # ─── sync：BFS crawl，state 里维护 visited set + pages map ───
    async def sync(self, opts):
        visited = set(await self.state.get("visited") or [])
        pages = await self.state.get("pages") or {}
        if opts.full:
            visited.clear()
            pages.clear()

        queue = list(self.config.start_urls)
        crawled = 0
        while queue and crawled < self.config.max_pages:
            url = queue.pop(0)
            if url in visited or not self._allowed(url):
                continue
            visited.add(url)
            etag_old = pages.get(self._url_to_path(url), {}).get("etag")
            resp = await self._fetch(url, if_none_match=etag_old)
            if resp.status == 304:
                continue
            if resp.status == 200:
                path = self._url_to_path(url)        # URL canonicalization，详见 §10.7
                md = self._html_to_md(resp.body)
                # state 里只存小指纹（url + etag + size），不存正文 md——
                # 正文是派生产物，归 converted_md artifact cache（框架在 read/index 时写）。
                pages[path] = {"url": url, "etag": resp.etag, "size": len(md)}
                yield ObjectChange(path, "modified")
                queue.extend(self._extract_links(resp.body, url))
            crawled += 1
            if crawled % 20 == 0:
                # visited 集合 + pages map 是 "单调推进 set + 关联 map"，可 checkpoint
                await self.state.set("visited", list(visited))
                await self.state.set("pages", pages)
                await self.state.checkpoint()

        await self.state.set("visited", list(visited))
        await self.state.set("pages", pages)

    def object_kind_of(self, path):
        return "document" if path.endswith(".md") else "directory"
```

注意：

- **不实现 `read_records`**——web page 不是结构化数据，bytes 自然形态
- **`self.state` 里只放小指纹，不放正文**——`pages` map 是 `{path: {url, etag, size}}`，正文 markdown 归 `converted_md` artifact cache（[05 §10.2](05-browse-and-read.md#10-artifact-cache-层细节)），framework 在 read/index 时写。connector_state 是给 cursor / etag 这种小状态的，别把派生字节塞进来（[§11](#11-边界规则)）
- **`list` 自维护 path tree**——枚举 state 里 `pages` map 的 path prefix。framework 不提供 path tree helper（每个动态 connector 自己几行实现），如果常见可以 v0.5+ 抽出 `VirtualPathTree` helper。v0.4 这张 map 受 `max_pages`（默认 1000）约束、规模可控；超大爬虫语料的更优存储（让 framework 的 `objects` 表反向开放只读、connector 不再自己存）留 v0.5+
- **checkpoint 是合法的**——`visited` 集合单调推进，`pages` map 跟 visited 同步增长，下次接续会跳过 visited，BFS 续跑（详见 [04 §5.6.1](04-connector-and-ingest.md#561-哪些-state-能调-checkpoint)）
- **HTML→markdown 走 markitdown**——是 fetch backend 的一部分（`static` backend = aiohttp 抓 HTML + markitdown 转 md），由 connector 内联做（跟 PDF/DOCX 那种"connector 吐原文件字节、框架 converter 转"不同——web 的转换跟 backend 绑定）。转出来的 md 进 `converted_md` artifact cache，重抓靠 etag 304 跳过。库版本在 pyproject pin。v0.4 默认抓**静态 / SSR HTML**（够覆盖多数文档站）；JS-heavy SPA 留 v0.5+，届时可选 `crawl4ai` backend（`mfs-server[web-crawl4ai]`，自带 JS 渲染 + markdown 抽取）

### 6.3 用户体验

```bash
# Postgres
mfs add postgres://prod --config .mfs/connectors/prod.toml
mfs ls postgres://prod/public/tickets
# file  schema.json     application/json     2.1 KB
# file  rows.jsonl      application/x-ndjson ~1.2 GB   ~12.4M rows (lazy)
mfs grep "ERR_TOKEN" postgres://prod/public/tickets/rows.jsonl
# 走 SQL ILIKE 下推，1 RPC 几秒返回

# Web
mfs add web://acme-docs --config .mfs/connectors/acme-docs.toml
mfs tree web://acme-docs/pages -L 2
# pages/
# └── docs.acme.com/
#     ├── Guide/
#     ├── api/
#     └── index.md
mfs cat web://acme-docs/pages/docs.acme.com/Guide/Start.md
```

## 7. 注册 plugin

`connectors/__init__.py` 维护注册表：

```python
from .file import FilePlugin
from .postgres import PostgresPlugin
from .slack import SlackPlugin
from .web import WebPlugin
from .github import GitHubPlugin
# ...

REGISTRY = {
    cls.URI_SCHEME: cls
    for cls in [FilePlugin, PostgresPlugin, SlackPlugin, WebPlugin, GitHubPlugin]
}
```

framework 根据 URI 的 scheme 查表实例化。

## 8. 测试要求

每个 connector 必须有：

### contract test

framework 提供 `tests/connectors/_contract.py`，对任意 plugin 跑一组通用 assertion：

```python
@pytest.mark.parametrize("plugin", [PostgresPlugin, SlackPlugin, ...])
async def test_connector_contract(plugin):
    # stat 必须返回有效 PathStat
    # list 返回 list[Entry]，按 name 排序
    # read 范围内可重入
    # fingerprint 同 input 同 output
    # sync 在无变化时不 yield 任何 ObjectChange
    # ...
```

新 connector 跑通这个就 PR-ready。

### fake connector 集成测试

不要求真连外部系统。提供 fixture-based fake：

```python
# tests/connectors/postgres/fixtures/tickets.sql
# tests/connectors/postgres/test_e2e.py

async def test_postgres_end_to_end(fake_postgres):
    plugin = PostgresPlugin(config=..., credential=...)
    # mfs add → 检查 objects 表
    # mfs ls → 检查列表
    # mfs head → 检查样本
    # mfs search → 检查 retrieval index
```

CI 自动跑 contract + fake。真连测试可选。

## 9. PR checklist

| 项 | 必须 |
|---|---|
| `connectors/<name>/` 下面文件齐全（plugin/config/connector/layout/sync/PROMPT.md/tests） | ✅ |
| `CONFIG_SCHEMA` 用 pydantic | ✅ |
| `CAPABILITIES` 准确（不撒谎说支持某能力但实际报错） | ✅ |
| `PROMPT.md` 写清 root 下面有什么对象、cat 行为、限制 | ✅ |
| `object_kind_of(path)` 覆盖该 connector 暴露的所有 path 模式 | ✅ |
| `fingerprint(path)` + `sync()` 实现增量 | ✅ |
| `self.state` 里存的 schema 在 connector 内部文档化（供自己维护） | ✅ |
| 对象命名遵循 §10 的规范 | ✅ |
| contract test 全过 | ✅ |
| fake E2E test 至少跑通 add / ls / head / search | ✅ |
| docstring 提到所需 OAuth scope / 权限 | ✅ |
| 不在 `objects/` 加新 kind（如确实需要新 kind 先开 RFC） | ✅ |
| 不在 `pipeline/` 改通用组件 | ✅ |
| `mfs-server[<name>]` extra 声明 SDK 依赖 | ✅ |

## 10. 对象命名规范

每个 connector 决定自己 root 下面有什么 object、叫什么名字、什么 media_type。下面是必须遵守的规范。

### 10.1 文件名按数据形态选后缀

| 数据形态 | 后缀 | 例 |
|---|---|---|
| 多条结构化记录的集合 | `.jsonl` | `rows.jsonl`、`messages.jsonl`、`issues.jsonl`、`records.jsonl`、`comments.jsonl`、`users.jsonl` |
| 单个 schema / 元数据描述 | `.json` | `schema.json`、`database.json`、`workflows.json`、`index.json` |
| 长文本对象（connector 自己生成的） | `.md` | `pages/<url>.md`（web）、`<id>.md`（notion） |
| 真实文件 | 保留原文件名和原后缀 | `README.md`、`config.toml`、`chart.png`、`app.jsonl` |
| 目录节点 | 无后缀，路径末尾 `/` | `tables/`、`channels/`、`pulls/42/`、`pages/` |

### 10.2 集合用 JSONL，不要造目录里全是单 JSON

不要：

```text
tickets/
  1.json
  2.json
  3.json
  ...
```

scale 不好，`ls` 巨慢，agent 没法 head/grep 整个集合。

要：

```text
tickets/
  schema.json                  # 元数据描述
  records.jsonl                # 全部 ticket，一行一个
  comments.jsonl               # 全部 comment（带 ticket_id 反向引用）
```

取单条用 `mfs cat tickets/records.jsonl --locator '{"id":12}'` 或 `export + jq`。

### 10.3 单 record 走 locator，不暴露成 path

不要给单条 record / row / issue 分配独立 path。它们由搜索结果的 `locator` JSON 定位，详见 [06 §3](06-search-and-retrieval.md#3-locator-schema-per-connector)。

例外：单条对象天然有持久 path 且数量可控时可以暴露（如 GitHub PR `pulls/42/diff.patch`）。

### 10.4 目录节点不能 cat

`cat` 目录返回 `is_directory` 错误。所有目录节点统一行为，不要在某些 connector 让 `cat dir/` 返回"目录概览"——那是 `ls` 的事。

### 10.5 真实文件透传

GitHub blob、S3 object、Drive file、本地文件这些真有文件实体的对象：

- 保留原文件名和后缀
- 不在路径上"装饰"任何东西
- `cat` 返回原始 bytes（除非是 PDF / DOCX 等 framework 知道怎么转 markdown 的类型）

### 10.6 命名词汇约定

常见对象的统一命名（每类 connector 暴露同概念时尽量用同名）：

| 概念 | 推荐名 |
|---|---|
| schema 描述 | `schema.json` |
| 全集合数据 | `<concept>s.jsonl`，复数（`rows`、`messages`、`issues`、`records`、`comments`、`users`、`threads`、`activities`） |
| 跨集合的元数据概览 | `database.json`、`workflows.json`、`index.json` |
| 当天 / 当前 partition | 路径段 `<yyyy-mm-dd>/`、`today/` 别名 |
| 附件目录 | `files/` |
| 真实文件 | 原名 |

### 10.7 URL → path 规范化（web / crawler 类 connector 必须遵守）

把 URL 直接当文件名用会撞：不同的 URL 可能映射到同一虚拟 path，导致 object_uri 撞 → chunk_id 撞 → 后写的覆盖先写的。把 URL 映射成 path 的 connector（web、Notion 公开页、Confluence、知识库爬虫等）必须遵守下面的规范化规则：

```
URL: https://docs.acme.com/Guide/Start?lang=zh#install
                            ↓
                    URL canonicalization
                            ↓
1. scheme + host 小写、丢端口（仅当是默认端口 :80/:443）
2. path：去 trailing slash（除非 path 就是 "/"）
3. fragment（#anchor）：丢掉（同页不同锚不算不同对象）
4. query：保留有意义参数；丢掉常见 tracking（utm_*、fbclid、gclid）
   保留下来的参数按 key 字典序排序：lang=zh&page=2
5. percent-encoding：normalize 大小写（%2F → %2F；不要 %2f）
6. path 段中的非安全字符（"?", "/", ":" 等 percent-encode）
                            ↓
canonical URL: https://docs.acme.com/Guide/Start?lang=zh
                            ↓
                    映射到 virtual path
                            ↓
pages/docs.acme.com/Guide/Start__q=lang=zh.md
```

映射规则：

| URL 部分 | 进 virtual path 的形式 |
|---|---|
| host | 作为第一级目录段（`pages/<host>/`） |
| path | 按 `/` 切，保留大小写（host 用 lowercase，path 保留原样） |
| query（非空） | 用 `__q=<sorted_kv>` 后缀挂在最后一段，多个参数用 `&` 分隔 |
| 文件名末尾后缀 | 默认 `.md`（页面转 markdown 后） |

例子：

| URL | virtual path |
|---|---|
| `https://docs.acme.com/` | `pages/docs.acme.com/index.md` |
| `https://docs.acme.com/Guide/Start` | `pages/docs.acme.com/Guide/Start.md` |
| `https://docs.acme.com/Guide/Start/` | `pages/docs.acme.com/Guide/Start.md`（trailing slash 不区分） |
| `https://docs.acme.com/Guide/Start?lang=zh` | `pages/docs.acme.com/Guide/Start__q=lang=zh.md` |
| `https://docs.acme.com/Guide/Start#install` | `pages/docs.acme.com/Guide/Start.md`（fragment 丢掉） |
| `https://docs.acme.com/Guide/Start?utm_source=x` | `pages/docs.acme.com/Guide/Start.md`（utm_ 丢掉） |

特殊情况：path 段超过 200 字节或含 FS 禁用字符（Windows 上的 `< > : " | ? *`）→ 截断 + 加 `__h=<sha1[:8]>` 后缀防碰撞。

原 URL 保存在 `objects.extra_json.url` 里供 cat 渲染 / 给 agent 看，不要从 virtual path 反推。

## 11. 边界规则

| 想做的事 | 能做吗 |
|---|---|
| 给 `chunk_kind` 加新值 | 不行，8 种固定，要加走 framework RFC |
| 给 `object_kind` 加新值 | 不行，同上 |
| 在 connector 里直接写 Milvus | 不行，走 framework pipeline |
| 在 connector 里直接调 OpenAI embedding | 不行，同上 |
| 在 connector 里读 `~/.mfs/cache/` | 不行，走 framework storage adapter |
| 在 connector 里写定时调度 | v0.4 不内置 scheduler，周期刷新靠用户系统 cron 调 `mfs add`（见 04 §9）|
| 暴露不在 PROMPT.md 描述里的 path | 不行，暴露 = 文档化 |
| 自定义 `namespace_id` 行为 | 不行，由 framework 注入 |
| 用新的 URI scheme（如 `myco://`） | 可以，注册即可 |
| 让 cat 渲染特殊格式 | 可以，在 `object_kind_of` 标合适的 kind 用 framework handler |
| 在 `self.state` 里存任意 schema | 可以，由 connector 自己定义（cursor / etag map / 任意小 JSON 状态），framework 不 introspect。**注意**：大规模 path/object 级别的全量映射不要塞 `self.state`——v0.4 用 `self.state` K/V 装这种数据会很重。file connector 就是因此走专属的 `file_state` 结构化表（详见 04 §5.5），是 framework 唯一的特例 |
| 用 `task_priority` 控制 object 索引顺序 | 可以（可选），返回 int 越小越先，不写默认 FIFO |

## 12. 写 connector 前的设计检查

写第一行代码前先回答：

1. connector root 下要暴露哪些 object？
2. 每个 object 是什么 media_type、什么 object_kind？
3. 列目录 / 读对象的成本如何？需要 artifact cache 吗？
4. 怎么判断对象变化？fingerprint 算什么？
5. 哪些对象要索引（进 chunk）？text_fields 默认是什么？
6. 能否下推 grep / search / tail？
7. **upstream 能不能识别 delete？属于 `delete_detection` 的哪一档**（`never` / `explicit` / `full_scan` / `state_change`）？详见 [02 §7.4](02-architecture.md#74-deletion-策略)
8. 凭据是什么？OAuth scope 要哪些？
9. 用户必填配置最少是什么？

回答完了再开始写。
