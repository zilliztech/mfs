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
    CONTENT_MAX,
    Chunk,
    END_OF_TASK,
    ObjectTask,
    ProducedItem,
    ProducerContext,
)


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
                yield Chunk(
                    content=summ,
                    chunk_kind="schema_summary",
                    locator=None,
                    uri=full_uri,
                    connector_job_id=task.connector_job_id,
                    partial=len(summ) > CONTENT_MAX,
                )
        yield END_OF_TASK
