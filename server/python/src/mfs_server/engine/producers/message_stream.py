"""MessageStreamProducer — slack / discord / feishu / gmail -> thread_aggregate chunks.

A message API (e.g. Slack `conversations.history`) returns messages newest-first, so a
thread's root and its replies can be hundreds of messages apart — pure streaming chunk
isn't possible (§5.4). Two passes:

  1. **Materialize**: stream records from the connector and append each as a jsonl line
     to a temp `raw_records` artifact, keeping only a `thread_key -> [(offset, length)]`
     map in memory (~100 B/message). Peak memory is metadata-only, independent of
     channel size.
  2. **Regroup by thread**: in enumeration order, stream each thread's messages back from
     the jsonl by offset, render + size-split them, and yield one thread_aggregate chunk
     (short thread) or several (long thread, split at message boundaries with overlap).

Post-materialization the stream is structurally identical to a file: read from a local
file, yield chunks. The raw_records artifact is transient (GC'd after the task).
"""

from __future__ import annotations

import json
import os
from typing import AsyncIterator

from .base import (
    Chunk,
    END_OF_TASK,
    EndOfTask,
    ObjectTask,
    ProducedItem,
    ProducerContext,
)
from .render import render_record, split_thread
from .text import chunk_text_body

# Auto-detected thread keys, in priority order, when no [[objects]] group_by is set.
_THREAD_KEYS = ("thread_ts", "threadId", "thread_id", "thread")


class MessageStreamProducer:
    """message_stream -> thread_aggregate chunks (thread-grouped)."""

    def __init__(self, ctx: ProducerContext):
        self.ctx = ctx

    async def produce(self, task: ObjectTask) -> AsyncIterator[ProducedItem]:
        ns = self.ctx.namespace_id
        full_uri = task.full_uri
        ocfg = task.config()
        records = task.plugin.read_records(task.object_uri)
        if records is None or not ocfg.text_fields:
            yield END_OF_TASK
            return

        cfg_key = ocfg.group_by
        group_key = cfg_key or "thread"
        path = self.ctx.artifacts.artifact_path(ns, full_uri, "raw_records")

        # --- pass 1: materialize to jsonl, keep only the thread -> offsets map ---
        order: list = []
        groups: dict = {}  # group value -> [(byte_offset, byte_length)]
        truncated = False
        # the artifact path may sit under a per-object dir the cache only mkdirs on
        # put_artifact; ensure it exists since we stream-write the file ourselves.
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        try:
            with open(path, "wb") as f:
                async for rec in records:
                    if cfg_key:
                        gk = rec.get(cfg_key)
                    else:
                        gk = next((rec[k] for k in _THREAD_KEYS if rec.get(k)), None)
                    gk = gk or rec.get("ts") or rec.get("id") or str(len(order))
                    if gk not in groups:
                        if len(order) >= ocfg.chunk_max:
                            truncated = True
                            break
                        groups[gk] = []
                        order.append(gk)
                    line = (json.dumps(rec, default=str, ensure_ascii=False) + "\n").encode()
                    off = f.tell()
                    f.write(line)
                    groups[gk].append((off, len(line)))
        finally:
            aclose = getattr(records, "aclose", None)
            if aclose is not None:
                await aclose()

        # --- pass 2: regroup by thread, stream each thread back from the jsonl ---
        chunk_size = self.ctx.cfg.chunking.chunk_size
        with open(path, "rb") as f:
            for gk in order:
                rendered: list[str] = []
                for off, length in groups[gk]:
                    f.seek(off)
                    rec = json.loads(f.read(length))
                    r = render_record(rec, ocfg.text_fields, ocfg.render_template)
                    if r.strip():
                        rendered.append(r)
                # split_thread breaks ONLY at message boundaries, so a single oversized
                # message stays whole in one sub-chunk. Run each sub-chunk through the SAME
                # document chunker (force-split HARD cap) so no chunk can exceed chunk_size
                # and OOM the embedder; a normally-sized sub-chunk fits in one part and
                # passes through unchanged, preserving thread semantics.
                flat: list[tuple[int, int, str]] = []
                for s, e, text in split_thread(rendered):
                    for ctext, _lines in chunk_text_body(text, "document", "", chunk_size):
                        flat.append((s, e, ctext))
                if len(flat) == 1:
                    # short thread: single-chunk locator shape (preserves cat/search semantics)
                    yield Chunk(
                        content=flat[0][2],
                        chunk_kind="thread_aggregate",
                        locator={group_key: gk},
                        uri=full_uri,
                        connector_job_id=task.connector_job_id,
                        partial=False,
                    )
                else:
                    # long thread: a single running index keeps each chunk's locator unique
                    # across both the message-boundary split and any force-split.
                    for ci, (s, e, text) in enumerate(flat):
                        yield Chunk(
                            content=text,
                            chunk_kind="thread_aggregate",
                            locator={group_key: gk, "chunk_index": ci, "msg_range": [s, e]},
                            uri=full_uri,
                            connector_job_id=task.connector_job_id,
                            partial=False,
                        )
        was_capped = task.plugin.ctx.was_partial(task.object_uri)
        yield EndOfTask(partial=truncated or was_capped)
