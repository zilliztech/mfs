"""RecordCollectionProducer — table_rows / record_collection -> row_text chunks.

Per-row streaming: each record renders to one chunk via text_fields and is yielded
immediately, so the cursor/connection is released as we go and the whole table is never
held in memory (this is the OOM fix of §1.1 — no buffered pairs/vecs/rows). The first
HEAD_CACHE_N raw records are pre-cached as a head_cache artifact so `head` is fast.

table_rows (SQL rows) and record_collection (issues / mongo docs) share this path; the
JSONPath-lite resolver handles nested arrays (`comments[].body`) for both.
"""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator

from .base import (
    Chunk,
    EndOfTask,
    HEAD_CACHE_N,
    ObjectTask,
    ProducedItem,
    ProducerContext,
)
from .render import field_top_key, render_record, resolve_path
from .text import chunk_text_body

logger = logging.getLogger(__name__)


class RecordCollectionProducer:
    """table_rows / record_collection -> row_text chunks (streaming, per record)."""

    def __init__(self, ctx: ProducerContext):
        self.ctx = ctx

    async def produce(self, task: ObjectTask) -> AsyncIterator[ProducedItem]:
        ns = self.ctx.namespace_id
        full_uri = task.full_uri
        ocfg = task.config()
        records = task.plugin.read_records(task.object_uri)
        if records is None:
            yield EndOfTask()
            return
        if not ocfg.text_fields:
            # Not a misconfiguration on its own — an object legitimately having no
            # text_fields mapping is a valid setup (e.g. this object type isn't meant
            # to be searchable). Log a breadcrumb, not a warning, so it's still visible
            # to whoever's watching but doesn't read as "something's wrong here."
            logger.info(
                "%s: no text_fields configured — 0 chunks (set [[objects]].text_fields "
                "if this object should be searchable)",
                full_uri,
            )
            yield EndOfTask()
            return

        head_buf: list[str] = []  # first N raw records -> head_cache artifact
        # Track whether each text_field's KEY ever appears, to tell a schema mismatch
        # (key absent everywhere -> field_missing) from a legitimately empty value.
        text_top_keys = {f: field_top_key(f) for f in ocfg.text_fields}
        seen_field_keys: set[str] = set()
        i = 0
        emitted = 0
        truncated = False
        try:
            async for rec in records:
                if len(head_buf) < HEAD_CACHE_N:
                    head_buf.append(json.dumps(rec, default=str, ensure_ascii=False))
                if isinstance(rec, dict):
                    for f, tk in text_top_keys.items():
                        if tk in rec:
                            seen_field_keys.add(f)
                loc = (
                    {f: resolve_path(rec, f) for f in ocfg.locator_fields}
                    if ocfg.locator_fields
                    else {"_row": i}
                )
                meta = (
                    {f: resolve_path(rec, f) for f in ocfg.metadata_fields}
                    if ocfg.metadata_fields
                    else {}
                )
                text = render_record(rec, ocfg.text_fields, ocfg.render_template)
                i += 1
                if text.strip():
                    # A single record can be huge when text_fields aggregate an array
                    # (e.g. a jira issue = summary + description + comments[].body). Run it
                    # through the SAME chunker as documents (force-split HARD cap) so it
                    # becomes one or more <= chunk_size chunks instead of one giant chunk
                    # that OOMs the embedder. The common case (small record) yields exactly
                    # one part and keeps the original locator; only a record that actually
                    # splits gets a chunk_index suffix to keep each chunk's locator unique.
                    parts = chunk_text_body(text, "document", "", self.ctx.cfg.chunking.chunk_size)
                    multi = len(parts) > 1
                    for ci, (ctext, _lines) in enumerate(parts):
                        yield Chunk(
                            content=ctext,
                            chunk_kind="row_text",
                            locator={**loc, "chunk_index": ci} if multi else loc,
                            metadata=meta,
                            uri=full_uri,
                            connector_job_id=task.connector_job_id,
                            partial=False,
                        )
                    emitted += 1
                    if emitted >= ocfg.chunk_max:
                        truncated = True
                        break
        finally:
            # release the record generator (cursor/connection) — also so a chunk_max
            # break doesn't leak a held connection.
            aclose = getattr(records, "aclose", None)
            if aclose is not None:
                await aclose()

        # Schema mismatch: records exist but NONE of the configured text_fields' keys are
        # present in any of them -> we produced 0 chunks. Do NOT raise: a producer that raises
        # mid-stream leaves the EmbedConsumer without an EndOfTask, leaking its per-task pending
        # state and wedging the task. Emit a partial EndOfTask so the engine records the object
        # as partial / not-indexed, and log the misconfiguration so the user can fix
        # [[objects]].text_fields. (When a chunk was emitted a field key was necessarily
        # present, so seen_field_keys is non-empty and this branch is skipped.)
        if i > 0 and ocfg.text_fields and not seen_field_keys:
            logger.warning(
                "%s: configured text_fields %s are absent from every record "
                "(indexed 0 chunks) — check [[objects]].text_fields",
                full_uri,
                list(ocfg.text_fields),
            )
            yield EndOfTask(partial=True)
            return
        if head_buf:
            await self.ctx.artifacts.put_artifact(
                ns, full_uri, "head_cache", ("\n".join(head_buf)).encode()
            )
        # partial if chunk_max truncated OR the connector capped its read.
        was_capped = task.plugin.ctx.was_partial(task.object_uri)
        # Breadcrumb on the normal path too — a deliberate departure from this
        # producer's (and its sibling producers') usual silence-on-success
        # convention, so `emitted` is visible without needing chunk_count from a
        # separate inspect call.
        logger.info(
            "%s: emitted %d chunk(s) from %d record(s) using text_fields %s",
            full_uri,
            emitted,
            i,
            list(ocfg.text_fields),
        )
        yield EndOfTask(partial=truncated or was_capped)
