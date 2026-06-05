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
file, yield chunks. The raw_records artifact is transient (GC'd after the task; wired in
step 6).
"""

from __future__ import annotations

import json
from typing import AsyncIterator

from .base import (
    CONTENT_MAX,
    Chunk,
    END_OF_TASK,
    EndOfTask,
    ObjectTask,
    ProducedItem,
    ProducerContext,
)
from .render import render_record, split_thread

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
        try:
            with open(path, "wb") as f:
                async for rec in records:
                    if cfg_key:
                        gk = rec.get(cfg_key)
                    else:
                        gk = next((rec[k] for k in _THREAD_KEYS if rec.get(k)), None)
                    gk = gk or rec.get("ts") or rec.get("id") or str(len(order))
                    if gk not in groups:
                        groups[gk] = []
                        order.append(gk)
                    line = (json.dumps(rec, default=str, ensure_ascii=False) + "\n").encode()
                    off = f.tell()
                    f.write(line)
                    groups[gk].append((off, len(line)))
                    if len(order) >= ocfg.chunk_max:
                        break
        finally:
            aclose = getattr(records, "aclose", None)
            if aclose is not None:
                await aclose()

        # --- pass 2: regroup by thread, stream each thread back from the jsonl ---
        content_truncated = False
        with open(path, "rb") as f:
            for gk in order:
                rendered: list[str] = []
                for off, length in groups[gk]:
                    f.seek(off)
                    rec = json.loads(f.read(length))
                    r = render_record(rec, ocfg.text_fields, ocfg.render_template)
                    if r.strip():
                        rendered.append(r)
                sub = split_thread(rendered)
                if len(sub) == 1:
                    # short thread: single-chunk locator shape (preserves cat/search semantics)
                    text = sub[0][2]
                    was_trunc = len(text) > CONTENT_MAX
                    content_truncated = content_truncated or was_trunc
                    yield Chunk(
                        content=text,
                        chunk_kind="thread_aggregate",
                        locator={group_key: gk},
                        uri=full_uri,
                        connector_job_id=task.connector_job_id,
                        partial=was_trunc,
                    )
                else:
                    # long thread: tag each sub-chunk with its position WITHIN the thread
                    for sub_i, (s, e, text) in enumerate(sub):
                        was_trunc = len(text) > CONTENT_MAX
                        content_truncated = content_truncated or was_trunc
                        yield Chunk(
                            content=text,
                            chunk_kind="thread_aggregate",
                            locator={group_key: gk, "chunk_index": sub_i, "msg_range": [s, e]},
                            uri=full_uri,
                            connector_job_id=task.connector_job_id,
                            partial=was_trunc,
                        )
        yield EndOfTask(partial=content_truncated)
