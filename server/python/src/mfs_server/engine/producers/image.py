"""ImageChunksProducer — image okind -> one vlm_description chunk (§5.5).

The VLM call goes through the process-shared description_gate (a ConcurrencyGate)
so at most [description].concurrency calls are in flight at once, and through the
CachingVlmClient (transformation-cache memoized, so a repeated image skips the LLM).
The description is also persisted as a vlm_text artifact for `mfs cat`.
"""

from __future__ import annotations

from typing import AsyncIterator

from .base import (
    CONTENT_MAX,
    Chunk,
    END_OF_TASK,
    ObjectTask,
    ProducedItem,
    ProducerContext,
    read_bytes,
)


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
            yield Chunk(
                content=desc,
                chunk_kind="vlm_description",
                locator=None,
                uri=full_uri,
                connector_job_id=task.connector_job_id,
                partial=len(desc) > CONTENT_MAX,
            )
        yield END_OF_TASK
