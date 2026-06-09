"""TableSchemaProducer — table_schema okind -> one schema_summary chunk.

Reads the first (schema) record from the connector, asks the summary LLM to describe
the table/collection schema for search, and yields a single schema_summary chunk. The
LLM call goes through the process-shared summary_gate (ConcurrencyGate) and the
CachingSummaryClient (transformation-cache memoized).

Whether table_schema is produced at all is a dispatch decision ([summary] gating, step
4); the producer itself just produces when invoked.
"""

from __future__ import annotations

import json
from typing import AsyncIterator

from .base import (
    Chunk,
    END_OF_TASK,
    ObjectTask,
    ProducedItem,
    ProducerContext,
)
from .text import chunk_text_body


class TableSchemaProducer:
    """table_schema -> schema_summary chunk."""

    def __init__(self, ctx: ProducerContext):
        self.ctx = ctx

    async def produce(self, task: ObjectTask) -> AsyncIterator[ProducedItem]:
        full_uri = task.full_uri
        records = task.plugin.read_records(task.object_uri)
        schema_obj = None
        if records is not None:
            try:
                async for r in records:
                    schema_obj = r
                    break
            finally:
                # Breaking after the first record does NOT close the async generator,
                # so a connector that yields from inside `async with pool.acquire()`
                # would pin a connection; aclose() releases it.
                aclose = getattr(records, "aclose", None)
                if aclose is not None:
                    await aclose()

        if schema_obj is not None:
            text = json.dumps(schema_obj, default=str)
            async with self.ctx.summary_gate:
                summ = await self.ctx.summary.summarize(text, "schema_summary")
            if summ.strip():
                # A schema summary for a very wide table can be long; run it through the
                # SAME document chunker (force-split HARD cap) so no chunk exceeds chunk_size
                # and OOMs the embedder. The common case (short summary) yields one part with
                # the original locator=None; only a split summary gets a chunk_index.
                parts = chunk_text_body(summ, "document", "", self.ctx.cfg.chunking.chunk_size)
                multi = len(parts) > 1
                for ci, (ctext, _lines) in enumerate(parts):
                    yield Chunk(
                        content=ctext,
                        chunk_kind="schema_summary",
                        locator={"chunk_index": ci} if multi else None,
                        uri=full_uri,
                        connector_job_id=task.connector_job_id,
                        partial=False,
                    )
        yield END_OF_TASK
