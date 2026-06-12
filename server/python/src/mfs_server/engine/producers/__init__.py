"""ChunksProducer package — the Object Lane's per-okind producers (§3.2 / §5.3).

Each ObjectTask is dispatched by okind to one of five producers, which turn it into a
stream of Chunk objects + a trailing EndOfTask sentinel. dir_summary / file_summary are
NOT here — they belong to the Job Lane (§3.5).
"""

from __future__ import annotations

from .base import (
    CONTENT_MAX,
    HEAD_CACHE_N,
    ArtifactStore,
    Chunk,
    ChunksProducer,
    ConcurrencyGate,
    DescriptionConcurrencyGate,
    END_OF_TASK,
    EndOfTask,
    ObjectTask,
    ProducedItem,
    ProducerContext,
    SummaryConcurrencyGate,
    cap_content,
    read_bytes,
    read_text,
)
from .image import ImageChunksProducer
from .message_stream import MessageStreamProducer
from .record_collection import RecordCollectionProducer
from .table_schema import TableSchemaProducer
from .text import TextChunksProducer

# okind -> producer class (§3.2 dispatch). text_blob shares the text path (markdown
# recursive rules). table_rows / record_collection share the record path.
_OKIND_PRODUCER: dict[str, type] = {
    "document": TextChunksProducer,
    "code": TextChunksProducer,
    "text_blob": TextChunksProducer,
    "image": ImageChunksProducer,
    "message_stream": MessageStreamProducer,
    "table_rows": RecordCollectionProducer,
    "record_collection": RecordCollectionProducer,
    "table_schema": TableSchemaProducer,
}


def select_producer(okind: str, ctx: ProducerContext) -> ChunksProducer | None:
    """Build the ChunksProducer for an okind, or None when the okind carries no chunks
    (binary / directory). Enablement gating ([description]/[summary]) is the engine's
    routing job (_routes_to_pipeline), not this factory's."""
    cls = _OKIND_PRODUCER.get(okind)
    return cls(ctx) if cls is not None else None


__all__ = [
    "ArtifactStore",
    "CONTENT_MAX",
    "Chunk",
    "ChunksProducer",
    "ConcurrencyGate",
    "DescriptionConcurrencyGate",
    "END_OF_TASK",
    "EndOfTask",
    "HEAD_CACHE_N",
    "ImageChunksProducer",
    "MessageStreamProducer",
    "ObjectTask",
    "ProducedItem",
    "ProducerContext",
    "RecordCollectionProducer",
    "SummaryConcurrencyGate",
    "TableSchemaProducer",
    "TextChunksProducer",
    "cap_content",
    "read_bytes",
    "read_text",
    "select_producer",
]
