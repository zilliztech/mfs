"""Connector plugin contract (design/07 §3 §4).

A contributor implements 6 core methods (stat/list/read|read_records/fingerprint/
sync/object_kind_of) + optional overrides. Everything else (chunk/embed/Milvus/
retrieval/cache/HTTP/queue) is framework. No `acl` (dropped in v0.4). `PathStat`
(not FileStat) — the abstraction isn't file-specific.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Protocol

# --- object_kind (framework-fixed; design/02 processors + 06 §6) ---
ObjectKind = Literal[
    "document", "code", "image", "binary", "text_blob",
    "table_rows", "table_schema", "message_stream", "record_collection", "directory",
]

# --- chunk_kind (framework-fixed, 8 kinds; design/06 §2) ---
ChunkKind = Literal[
    "body", "row_text", "thread_aggregate", "record_aggregate",
    "summary", "vlm_description", "directory_summary", "schema_summary",
]

DeleteDetection = Literal["never", "explicit", "full_scan", "state_change"]
EnumerationMode = Literal["full", "incremental", "explicit_only"]

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def safe_ident(name: str) -> str:
    """Validate a SQL identifier (schema/table/column/object) before interpolating it
    into a query string. Connectors derive these from user-supplied paths (cat/head a
    structured URI), so an unvalidated name is an injection vector. Rejects anything
    outside [A-Za-z_][A-Za-z0-9_$]* — strict, but covers normal table/object names."""
    if not isinstance(name, str) or not _IDENT_RE.match(name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return name


@dataclass
class PathStat:
    path: str
    type: Literal["file", "dir"]
    media_type: Optional[str] = None
    size_hint: Optional[int] = None
    fingerprint: Optional[str] = None
    extra: dict = field(default_factory=dict)


@dataclass
class Entry:
    name: str
    type: Literal["file", "dir"]
    media_type: Optional[str] = None
    size_hint: Optional[int] = None
    extra: dict = field(default_factory=dict)


@dataclass
class Range:
    start: int
    end: int


@dataclass
class ObjectChange:
    uri: str
    kind: Literal["added", "modified", "deleted", "renamed"]
    old_uri: Optional[str] = None      # only for renamed


@dataclass
class SyncOptions:
    full: bool = False                 # user --force-index
    since: Optional[str] = None        # user --since <date>, overrides state cursor


@dataclass
class GrepMatch:
    path: str
    locator: Optional[dict] = None     # structured connectors (pk etc.)
    line_no: Optional[int] = None      # text connectors
    content: str = ""
    context_before: list[str] = field(default_factory=list)
    context_after: list[str] = field(default_factory=list)


@dataclass
class GrepOptions:
    pattern: str
    case_insensitive: bool = False
    context_lines: int = 0
    text_fields: list[str] = field(default_factory=list)       # injected from ObjectConfig
    metadata_fields: list[str] = field(default_factory=list)


@dataclass
class HealthStatus:
    ok: bool
    detail: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class Capabilities:
    # sync
    manual_sync: bool = True
    watch: bool = False
    cursor_kind: Optional[str] = None
    full_scan: bool = True
    delete_detection: DeleteDetection = "explicit"
    # object access
    grep_pushdown: bool = False
    search_pushdown: bool = False
    paged_cat: bool = True

    def to_dict(self) -> dict:
        return {
            "sync": {
                "manual": self.manual_sync, "watch": self.watch,
                "cursor": self.cursor_kind, "full_scan": self.full_scan,
                "delete_detection": self.delete_detection,
            },
            "object": {
                "grep_pushdown": self.grep_pushdown,
                "search_pushdown": self.search_pushdown,
                "paged_cat": self.paged_cat,
            },
        }


@dataclass
class ObjectConfig:
    """Parsed from connector TOML [[objects]] (design/06 §4). Framework-injected."""
    text_fields: list[str] = field(default_factory=list)
    metadata_fields: list[str] = field(default_factory=list)
    locator_fields: list[str] = field(default_factory=list)
    chunk_strategy: str = "per_row"        # per_row|per_group|per_field_chunked|windowed|sampled
    indexable: bool = True
    chunk_max: int = 1_000_000
    index_filter: Optional[str] = None     # restricted AST expr (NOT eval)
    text_template: Optional[str] = None
    group_by: Optional[str] = None
    session_idle_min: Optional[int] = None
    chunk_window: Optional[str] = None     # windowed, e.g. "30d"
    sample_rate: Optional[float] = None    # sampled, e.g. 0.01
    max_text_chars: Optional[int] = None


class StateStore(Protocol):
    """Persistent per-connector KV (connector_state table). Not in-memory."""
    async def get(self, key: str) -> Any | None: ...
    async def set(self, key: str, value: Any) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def checkpoint(self) -> None: ...    # cursor / monotonic-set state only


class ConnectorContext:
    """Framework-injected runtime context (design/07 §3)."""

    def __init__(self, state: StateStore, connector_id: str, namespace_id: str,
                 object_config_resolver=None):
        self.state = state
        self.connector_id = connector_id
        self.namespace_id = namespace_id
        self._resolver = object_config_resolver
        self.enumeration_mode: EnumerationMode = "incremental"   # default = safest

    def object_config_for(self, path: str) -> ObjectConfig:
        if self._resolver is None:
            return ObjectConfig()
        return self._resolver(path)

    def declare_enumeration(self, mode: EnumerationMode) -> None:
        """Connector declares this run's actual enumeration mode; framework uses it
        to decide whether full-set diff deletion is allowed (design/02 §7.4)."""
        self.enumeration_mode = mode


class ConnectorPlugin(ABC):
    # --- metadata (class attrs) ---
    NAME: str = ""
    URI_SCHEME: str = ""
    DISPLAY_NAME: str = ""
    PROMPT: str = ""
    CAPABILITIES: Capabilities = Capabilities()
    CONFIG_SCHEMA: Optional[type] = None

    def __init__(self, config: Any, credential: Any, *, ctx: ConnectorContext):
        self.config = config
        self.credential = credential
        self.state = ctx.state
        self.ctx = ctx

    # --- lifecycle ---
    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def healthcheck(self) -> HealthStatus:
        return HealthStatus(ok=True)

    # --- required: core IO ---
    @abstractmethod
    async def stat(self, path: str) -> PathStat: ...

    @abstractmethod
    async def list(self, path: str) -> list[Entry]: ...

    async def read(self, path: str, range: Optional[Range] = None) -> AsyncIterator[bytes]:
        """Byte stream for cat/head/tail/grep/export. If only read_records is
        implemented, framework wraps records as jsonl bytes here."""
        import json

        records = self.read_records(path, range)
        if records is None:
            raise NotImplementedError("either read or read_records must be implemented")
        async for r in records:
            yield (json.dumps(r, default=str) + "\n").encode()

    def read_records(self, path: str, range: Optional[Range] = None) -> Optional[AsyncIterator[dict]]:
        """Structured connectors override as async generator. Base returns None
        (plain def, so `is None` distinguishes implemented vs not)."""
        return None

    # --- required: change detection ---
    @abstractmethod
    async def fingerprint(self, path: str) -> Optional[str]: ...

    @abstractmethod
    def sync(self, opts: SyncOptions) -> AsyncIterator[ObjectChange]: ...

    # --- required: path classification ---
    @abstractmethod
    def object_kind_of(self, path: str) -> ObjectKind: ...

    # --- optional overrides (base defaults) ---
    async def grep(self, pattern: str, path: str, options: GrepOptions) -> Optional[AsyncIterator[GrepMatch]]:
        return None     # framework default dispatch (pushdown? no -> BM25/linear)

    async def search(self, query: str, path: str, options: Any) -> Optional[AsyncIterator[Any]]:
        return None     # framework default: Milvus recall

    def chunk_plan(self, path: str) -> Optional[dict]:
        return None

    def render(self, path: str, media_type: str) -> Optional[str]:
        return None

    def task_priority(self, change: ObjectChange) -> int:
        return 0

    # --- framework callbacks after a task completes (base no-op) ---
    async def on_object_indexed(self, uri: str) -> None:
        """Called by engine after an object's task succeeds. file connector overrides
        to flip file_state status='staged' -> 'indexed' (design/04 §5.5 step 6)."""
        return None

    async def on_object_deleted(self, uri: str) -> None:
        """Called by engine after a deletion task succeeds."""
        return None
