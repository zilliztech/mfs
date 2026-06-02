"""Connector plugin contract.

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

# --- object_kind (framework-fixed) ---
ObjectKind = Literal[
    "document",
    "code",
    "image",
    "binary",
    "text_blob",
    "table_rows",
    "table_schema",
    "message_stream",
    "record_collection",
    "directory",
]

# --- chunk_kind (framework-fixed, 8 kinds) ---
ChunkKind = Literal[
    "body",
    "row_text",
    "thread_aggregate",
    "record_aggregate",
    "summary",
    "vlm_description",
    "directory_summary",
    "schema_summary",
]

DeleteDetection = Literal["never", "explicit", "full_scan", "state_change"]
EnumerationMode = Literal["full", "incremental", "explicit_only"]

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


_TEXT_FIELD_NAME_PATTERN = re.compile(
    r"^("
    r"title|name|label|subject|summary|"
    r"description|details?|body|content|text|message|comment|notes?|memo|excerpt|abstract|"
    r"category|tag|reason|status|"
    r"html|markdown|caption"
    r")(_\w+)?$",
    re.IGNORECASE,
)


def pick_text_candidates(columns: list[dict]) -> list[str]:
    """Heuristic: from the column list, return likely text-field candidates.

    Order: name-matching string columns first (title/description/body/...),
    then any other string columns. Caller (wizard) picks the top few and
    prompts the user to confirm.

    Each column dict must have keys: name (str), type (str), and may
    optionally carry a 'pk' bool (ignored here).
    """
    string_types = (
        "varchar",
        "text",
        "char",
        "character",
        "string",
        "longtext",
        "mediumtext",
        "tinytext",
        "nvarchar",
        "ntext",
        "clob",
        "json",
        "jsonb",
        "object",
    )
    name_matches: list[str] = []
    other_strings: list[str] = []
    for c in columns:
        if c.get("pk"):
            continue  # PKs aren't text fields
        col_type = str(c.get("type", "")).lower()
        is_string = any(t in col_type for t in string_types)
        if not is_string:
            continue
        col_name = c["name"]
        if _TEXT_FIELD_NAME_PATTERN.match(col_name):
            name_matches.append(col_name)
        else:
            other_strings.append(col_name)
    return name_matches + other_strings


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
    old_uri: Optional[str] = None  # only for renamed


@dataclass
class SyncOptions:
    full: bool = False  # user --force-index
    since: Optional[str] = None  # user --since <date>, overrides state cursor
    dry_run: bool = False  # estimate pre-flight: enumerate only, NO state writes


@dataclass
class GrepMatch:
    path: str
    locator: Optional[dict] = None  # structured connectors (pk etc.)
    line_no: Optional[int] = None  # text connectors
    content: str = ""
    context_before: list[str] = field(default_factory=list)
    context_after: list[str] = field(default_factory=list)


@dataclass
class GrepOptions:
    pattern: str
    case_insensitive: bool = False
    context_lines: int = 0
    text_fields: list[str] = field(default_factory=list)  # injected from ObjectConfig
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
                "manual": self.manual_sync,
                "watch": self.watch,
                "cursor": self.cursor_kind,
                "full_scan": self.full_scan,
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
    """Parsed from connector TOML [[objects]]. Framework-injected.

    Chunking strategy is decided by object_kind (set by each connector), NOT by user
    config — table_rows / record_collection always go per-row, message_stream always
    goes per-thread (with internal size-bounded sub-chunking). The user-facing knobs
    here are just: which fields become embedding text / locator / metadata, whether
    to index at all, and an upper bound on chunks per object.
    """

    text_fields: list[str] = field(default_factory=list)
    metadata_fields: list[str] = field(default_factory=list)
    locator_fields: list[str] = field(default_factory=list)
    indexable: bool = True
    chunk_max: int = 1_000_000
    group_by: Optional[str] = None  # message_stream: override the auto-detected thread key
    # Optional Python str.format template applied per record before chunking.
    # Lets message presets render `alice: 部署炸了, 503 spike` instead of the
    # default labeled `text: 部署炸了, 503 spike`, which embeds better and
    # keeps speaker identity inside each message. Template placeholders are
    # record keys; for nested fields use {a[b]} (str.format dict syntax).
    # When None, _render_record falls back to the labeled rendering, with
    # the label suppressed when text_fields has exactly one bare entry.
    render_template: Optional[str] = None

    def __post_init__(self) -> None:
        # "lines" is a framework-reserved key inside the locator dict (body /
        # code / document chunks use {"lines": [start, end]}). A connector
        # [[objects]] config that tries to claim it would collide with the
        # body-chunk identity and break cat-by-locator dispatch.
        if "lines" in (self.locator_fields or ()):
            raise ValueError(
                "locator_fields contains the reserved key 'lines'; pick a "
                "different column name (it is owned by the framework for "
                "body/code/document chunks)."
            )


# Built-in presets for public SaaS / message connectors: users get a
# searchable index without writing [[objects]] config. Keys are <connector>.<object>.
PRESETS: dict[str, dict] = {
    "github.issues": dict(
        text_fields=["title", "body", "comments[].body"],
        metadata_fields=["state", "labels[*]", "author", "assignees[*]", "updated_at"],
        locator_fields=["number"],
    ),
    "github.pulls": dict(
        text_fields=["title", "body", "reviews[].body", "comments[].body"],
        metadata_fields=["state", "draft", "labels[*]", "author", "merged_at", "updated_at"],
        locator_fields=["number"],
    ),
    "slack.messages": dict(
        group_by="thread_ts",
        # render_template puts speaker identity inside the embedded chunk
        # text. The Slack `user` field is a U… id (resolving to real name
        # requires the users.jsonl side index, which is also synced now), so
        # the chunk reads like "U012345: 部署炸了". Still strictly more useful
        # than the bare "text: 部署炸了" label because the embedding can
        # learn the id↔user-row association and downstream tools can map ids
        # to names at display time.
        render_template="{user}: {text}",
        text_fields=["text"],
        metadata_fields=["channel", "user", "ts"],
        locator_fields=["thread_ts"],
    ),
    "slack.users": dict(
        # Workspace member directory: name handle, real name, display name,
        # title, email — all of these are useful search hits for "who is X"
        # or "who handles Y on the team". The /users.jsonl object is small
        # even for large workspaces (a few thousand users at most), so indexing
        # each row costs nothing and unlocks `mfs search "VP of Engineering"`
        # against the team directory.
        text_fields=[
            "name",
            "real_name",
            "profile.display_name",
            "profile.title",
            "profile.email",
        ],
        metadata_fields=["is_admin", "is_bot", "deleted", "tz"],
        locator_fields=["id"],
    ),
    "discord.messages": dict(
        # Discord's per-message records don't have a `thread_id` field — a
        # thread is a separate child channel (type=11/10/12) with its own id
        # and `parent_id`. Aggregating Slack-style by `thread_id` would either
        # miss everything (no such field on the record) or degenerate to
        # one-chunk-per-message (every id is unique). So each Discord message
        # is indexed as its own chunk, keyed by `id`. Thread channels (when
        # synced — see DiscordPlugin.sync) get their own `messages.jsonl`
        # objects under /channels/<parent>/threads/<thread>/, indexed the
        # same way.
        group_by="id",
        # Discord message records carry `author.username` directly, so the
        # rendered chunk reads as "alice: 部署炸了" — much stronger search
        # signal than the bare content.
        render_template="{author[username]}: {content}",
        text_fields=["content"],
        metadata_fields=["channel_id", "author", "timestamp"],
        locator_fields=["id"],
    ),
    "gmail.messages": dict(
        group_by="threadId",
        text_fields=["subject", "from", "to", "body", "snippet"],
        metadata_fields=["from", "to", "date", "labelIds[*]"],
        locator_fields=["threadId", "id"],
    ),
    "zendesk.tickets": dict(
        text_fields=["subject", "description"],
        metadata_fields=["status", "priority", "tags[*]", "updated_at"],
        locator_fields=["id"],
    ),
    "feishu.messages": dict(
        group_by="thread_id",
        # Feishu `sender` is the user open_id (not a readable name); same
        # trade-off as Slack — the embedded chunk reads as "<id>: ...".
        render_template="{sender}: {text}",
        text_fields=["text"],
        metadata_fields=["msg_type", "create_time", "sender"],
        locator_fields=["message_id"],
    ),
}


def preset_object_config(key: str) -> Optional["ObjectConfig"]:
    """Build an ObjectConfig from a named preset, dropping keys ObjectConfig doesn't model."""
    p = PRESETS.get(key)
    if not p:
        return None
    fields = ObjectConfig.__dataclass_fields__
    return ObjectConfig(**{k: v for k, v in p.items() if k in fields})


class StateStore(Protocol):
    """Persistent per-connector KV (connector_state table). Not in-memory."""

    async def get(self, key: str) -> Any | None: ...
    async def set(self, key: str, value: Any) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def checkpoint(self) -> None: ...  # cursor / monotonic-set state only


class ConnectorContext:
    """Framework-injected runtime context."""

    def __init__(
        self, state: StateStore, connector_id: str, namespace_id: str, object_config_resolver=None
    ):
        self.state = state
        self.connector_id = connector_id
        self.namespace_id = namespace_id
        self._resolver = object_config_resolver
        self.enumeration_mode: EnumerationMode = "incremental"  # default = safest
        self._partial: set[str] = set()  # objects a connector read only partially (cap hit)

    def object_config_for(self, path: str) -> ObjectConfig:
        if self._resolver is None:
            return ObjectConfig()
        return self._resolver(path)

    def declare_enumeration(self, mode: EnumerationMode) -> None:
        """Connector declares this run's actual enumeration mode; framework uses it
        to decide whether full-set diff deletion is allowed."""
        self.enumeration_mode = mode

    def declare_partial(self, path: str) -> None:
        """Connector signals it read `path` only partially (hit max_read_rows/API cap);
        framework marks the object's search_status='partial' so agents see incomplete
        recall."""
        self._partial.add(path)

    def was_partial(self, path: str) -> bool:
        return path in self._partial


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

    async def introspect_for_wizard(self) -> dict[str, dict]:
        """Per-object schema preview for the `mfs-server connector add` wizard.

        SQL-family connectors override this to surface each table /
        collection's columns + primary-key + heuristically-suggested
        text-field candidates so the wizard can pre-populate
        text_fields / locator_fields rather than ask the user to type them
        in by hand.

        Return shape:
            {
                "<object_path>": {
                    "columns": [{"name": "id", "type": "integer", "pk": True}, ...],
                    "pk": ["id"],
                    "text_candidates": ["title", "description"],
                },
                ...
            }

        Default {} = the wizard skips the per-table step for this scheme
        (file / web / SaaS connectors don't need a schema preview).
        """
        return {}

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

    def read_records(
        self, path: str, range: Optional[Range] = None
    ) -> Optional[AsyncIterator[dict]]:
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
    async def grep(
        self, pattern: str, path: str, options: GrepOptions
    ) -> Optional[AsyncIterator[GrepMatch]]:
        return None  # framework default dispatch (pushdown? no -> BM25/linear)

    async def search(self, query: str, path: str, options: Any) -> Optional[AsyncIterator[Any]]:
        return None  # framework default: Milvus recall

    def preset_for(self, path: str) -> Optional[str]:
        """Built-in preset KEY for this path, used when the user didn't
        configure [[objects]]. Returns a PRESETS key (e.g. 'github.issues') or None.
        SaaS / message connectors override."""
        return None

    def chunk_plan(self, path: str) -> Optional[dict]:
        return None

    def render(self, path: str, media_type: str) -> Optional[str]:
        return None

    def task_priority(self, change: ObjectChange) -> int:
        return 0

    # --- framework callbacks after a task completes (base no-op) ---
    async def on_object_indexed(self, uri: str) -> None:
        """Called by engine after an object's task succeeds. file connector overrides
        to flip file_state status='staged' -> 'indexed'."""
        return None

    async def on_object_deleted(self, uri: str) -> None:
        """Called by engine after a deletion task succeeds."""
        return None
