"""ChunksProducer protocol + shared types for the Object Lane.

A ChunksProducer turns one ObjectTask into a stream of `Chunk` objects followed
by an `EndOfTask` sentinel (per-object atomic boundary, see design §6.1). The
process-global ChunksProducer pool (§5.7) claims tasks from many connectors, so
per-task data — including the connector `plugin` — lives on the `ObjectTask`,
while process-global services (converter / vlm / summary / artifact store /
concurrency gates / cfg) live on the `ProducerContext` each producer is built
with.

Producers are deliberately free of Milvus / embedding: they only read + transform
+ yield. The per-object `delete_by_object` (§6.1) and embedding/upsert live in the
EmbedConsumer (pipeline.py), which consumes this stream. This keeps producers
self-contained and unit-testable in isolation.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional, Protocol, Union, runtime_checkable

from ...connectors.base import ObjectConfig

# Per-chunk content ceiling (Milvus VARCHAR / embedding input limit). A cap that
# actually cuts content means the tail is unsearchable, so producers flag the chunk
# `partial=True` and the consumer aggregates that into the object's search_status.
CONTENT_MAX = 65000

# First N raw records pre-cached per structured object so `head` is fast without
# re-querying the source (record_collection / message_stream materialization).
HEAD_CACHE_N = 100


def cap_content(text: str) -> tuple[str, bool]:
    """Return (text capped to CONTENT_MAX, whether the cap removed any content)."""
    return text[:CONTENT_MAX], len(text) > CONTENT_MAX


@dataclass
class Chunk:
    """One unit headed for Milvus — what `search` / `grep` can recall.

    `uri` is the full object uri (connector_uri + object_uri) and `connector_job_id`
    routes the chunk back to its job for per-task pending bookkeeping. `partial` marks
    a chunk whose content was capped at CONTENT_MAX (tail unsearchable)."""

    content: str
    chunk_kind: str
    locator: Optional[dict] = None
    metadata: dict = field(default_factory=dict)
    uri: Optional[str] = None
    connector_job_id: Optional[str] = None
    partial: bool = False


@dataclass(frozen=True)
class EndOfTask:
    """Sentinel marking the end of one ObjectTask's chunk stream (§6.1).

    The EmbedConsumer keeps a per-task pending counter; when it reaches zero AND an
    EndOfTask has been seen, the object_tasks row flips to 'succeeded'. `partial` is
    set when the object's recall is incomplete (chunk_max truncation / connector cap),
    folded into the object's search_status alongside any per-chunk `partial` flags."""

    partial: bool = False


# Canonical clean sentinel for the common (not-truncated) case. Producers yield this
# when nothing was truncated, or `EndOfTask(partial=True)` when it was.
END_OF_TASK = EndOfTask()

ProducedItem = Union[Chunk, EndOfTask]


@dataclass
class ObjectTask:
    """Per-object work unit handed to a ChunksProducer.

    Mirrors one `object_tasks` row plus the resolved connector plugin / object config.
    The plugin lives here (not on ProducerContext) because the producer pool is
    process-global and claims tasks across connectors (§5.7)."""

    object_uri: str  # connector-relative path, leading '/'
    connector_uri: str  # scheme prefix; full_uri = connector_uri + object_uri
    okind: str
    change_kind: str = "added"
    connector_job_id: Optional[str] = None
    task_id: Optional[str] = None
    plugin: Any = None  # ConnectorPlugin for this task's connector
    ocfg: Optional[ObjectConfig] = None

    @property
    def full_uri(self) -> str:
        return self.connector_uri + self.object_uri

    @property
    def ext(self) -> str:
        return os.path.splitext(self.object_uri)[1].lower()

    def config(self) -> ObjectConfig:
        return self.ocfg if self.ocfg is not None else ObjectConfig()


class ConcurrencyGate:
    """Max-in-flight limiter for slow provider calls (§5.5).

    Backed by asyncio.Semaphore but exposes a business name so call sites read as
    "the VLM/summary concurrency gate" rather than a raw semaphore. This is a
    max-in-flight ceiling, NOT a rate limiter (see §5.5)."""

    def __init__(self, concurrency: int):
        self._sem = asyncio.Semaphore(max(1, concurrency))

    async def __aenter__(self) -> "ConcurrencyGate":
        await self._sem.acquire()
        return self

    async def __aexit__(self, *exc: object) -> bool:
        self._sem.release()
        return False


class DescriptionConcurrencyGate(ConcurrencyGate):
    """Gate around image-description (VLM) calls — [description].concurrency."""


class SummaryConcurrencyGate(ConcurrencyGate):
    """Gate around summary (chat) calls — [summary].concurrency."""


@runtime_checkable
class ArtifactStore(Protocol):
    """Per-object derived-artifact store (converted_md / vlm_text / head_cache /
    raw_records). The engine's ArtifactStoreAdapter backs this with the real
    artifact_cache + its metadata row; tests back it with an in-memory fake."""

    async def put_artifact(
        self, namespace_id: str, object_uri: str, kind: str, data: bytes, currency: str = ""
    ) -> None: ...

    async def get_artifact(
        self, namespace_id: str, object_uri: str, kind: str
    ) -> Optional[bytes]: ...

    async def get_artifact_fresh(
        self, namespace_id: str, object_uri: str, kind: str, currency: str
    ) -> Optional[bytes]:
        """Return the artifact bytes only if its stored currency token matches `currency`
        (same source content + producer version); otherwise None, so the caller recomputes."""
        ...

    def artifact_path(self, namespace_id: str, object_uri: str, kind: str) -> str:
        """Filesystem path backing this artifact, for streaming materialization
        (message_stream writes its raw_records jsonl here incrementally)."""
        ...


@dataclass
class ProducerContext:
    """Process-global services shared by every ChunksProducer instance.

    `plugin` is intentionally NOT here — it is per-task (the producer pool is
    cross-connector, §5.7) and lives on ObjectTask. The converter / vlm / summary
    clients already memoize through the transformation cache, so producers reach the
    transformation cache transitively via them rather than holding it directly."""

    cfg: Any  # ServerConfig
    namespace_id: str
    artifacts: ArtifactStore
    converter: Any  # ConverterClient: async convert(data, ext) -> str
    vlm: Any  # CachingVlmClient: async describe(data, ext) -> str
    summary: Any  # CachingSummaryClient: async summarize(text, kind) -> str
    description_gate: ConcurrencyGate
    summary_gate: ConcurrencyGate


@runtime_checkable
class ChunksProducer(Protocol):
    """Turn one ObjectTask into a stream of Chunk + a trailing EndOfTask (§5.3)."""

    async def produce(self, task: ObjectTask) -> AsyncIterator[ProducedItem]: ...


async def read_bytes(plugin: Any, relpath: str) -> bytes:
    """Drain a connector's byte stream for one object."""
    buf = bytearray()
    async for chunk in plugin.read(relpath):
        buf += chunk
    return bytes(buf)


async def read_text(plugin: Any, relpath: str) -> str:
    return (await read_bytes(plugin, relpath)).decode("utf-8", errors="replace")
