"""ImageChunksProducer — image okind -> one vlm_description chunk (§5.5).

The VLM call goes through the process-shared description_gate (a ConcurrencyGate)
so at most [description].concurrency calls are in flight at once, and through the
CachingVlmClient (transformation-cache memoized, so a repeated image skips the LLM).
The description is also persisted as a vlm_text artifact for `mfs cat`.
"""

from __future__ import annotations

from typing import AsyncIterator

from .base import (
    Chunk,
    END_OF_TASK,
    ObjectTask,
    ProducedItem,
    ProducerContext,
    read_bytes,
)
from .text import chunk_text_body


class ImageChunksProducer:
    """image -> vlm_description chunk."""

    def __init__(self, ctx: ProducerContext):
        self.ctx = ctx

    async def produce(self, task: ObjectTask) -> AsyncIterator[ProducedItem]:
        ns = self.ctx.namespace_id
        full_uri = task.full_uri
        raw = await read_bytes(task.plugin, task.object_uri)
        async with self.ctx.description_gate:
            desc = await self.ctx.vlm.describe(raw, task.ext)
        await self.ctx.artifacts.put_artifact(ns, full_uri, "vlm_text", desc.encode())
        if desc.strip():
            # A VLM description is normally short, but route it through the SAME document
            # chunker (force-split HARD cap) so even a pathologically long description can
            # never exceed chunk_size and OOM the embedder. The common case yields one part
            # with the original locator=None; only a split description gets a chunk_index.
            parts = chunk_text_body(desc, "document", "", self.ctx.cfg.chunking.chunk_size)
            multi = len(parts) > 1
            for ci, (ctext, _lines) in enumerate(parts):
                yield Chunk(
                    content=ctext,
                    chunk_kind="vlm_description",
                    locator={"chunk_index": ci} if multi else None,
                    uri=full_uri,
                    connector_job_id=task.connector_job_id,
                    partial=False,
                )
        yield END_OF_TASK
