"""Unit tests for MessageStreamProducer — thread_ts grouping, long-thread split, jsonl."""

from __future__ import annotations

import os

from mfs_server.connectors.base import ObjectConfig
from mfs_server.engine.producers import Chunk, EndOfTask, MessageStreamProducer, ObjectTask

from _fakes import FakeArtifactStore, FakePlugin, build_ctx, collect

_SLACK_OCFG = ObjectConfig(
    text_fields=["text"],
    locator_fields=["thread_ts"],
    group_by="thread_ts",
    render_template="{user}: {text}",
)


def _task(plugin, ocfg=_SLACK_OCFG):
    return ObjectTask(
        object_uri="/general",
        connector_uri="slack://acme",
        okind="message_stream",
        connector_job_id="job1",
        plugin=plugin,
        ocfg=ocfg,
    )


async def test_thread_ts_grouping(tmp_path):
    # newest-first stream (as a real message API returns); replies of thread A are
    # separated from its root by thread B's message.
    records = [
        {"user": "U2", "text": "reply to A", "thread_ts": "A", "ts": "3"},
        {"user": "U9", "text": "B standalone", "thread_ts": "B", "ts": "2"},
        {"user": "U1", "text": "root of A", "thread_ts": "A", "ts": "1"},
    ]
    plugin = FakePlugin(records={"/general": records})
    store = FakeArtifactStore(tmp_path)
    ctx = build_ctx(artifacts=store)
    items = await collect(MessageStreamProducer(ctx), _task(plugin))
    chunks = [x for x in items if isinstance(x, Chunk)]

    # two threads -> two thread_aggregate chunks, grouped by thread_ts
    assert len(chunks) == 2
    assert all(c.chunk_kind == "thread_aggregate" for c in chunks)
    by_thread = {c.locator["thread_ts"]: c for c in chunks}
    assert set(by_thread) == {"A", "B"}
    # both of A's messages joined into A's chunk, in stream order
    a = by_thread["A"].content
    assert "U2: reply to A" in a and "U1: root of A" in a
    assert "B standalone" not in a
    assert by_thread["A"].uri == "slack://acme/general"
    assert isinstance(items[-1], EndOfTask)
    # raw_records jsonl was materialized to the artifact path
    assert os.path.exists(store.artifact_path("default", "slack://acme/general", "raw_records"))


async def test_long_thread_splits_into_subchunks(tmp_path):
    # one thread, many long messages -> exceeds _THREAD_MAX_CHARS -> multiple sub-chunks
    big = "word " * 80  # ~400 chars rendered per message
    records = [{"user": f"U{i}", "text": big, "thread_ts": "T", "ts": str(i)} for i in range(10)]
    plugin = FakePlugin(records={"/general": records})
    ctx = build_ctx(artifacts=FakeArtifactStore(tmp_path))
    items = await collect(MessageStreamProducer(ctx), _task(plugin))
    chunks = [x for x in items if isinstance(x, Chunk)]

    assert len(chunks) > 1
    for sub_i, c in enumerate(chunks):
        assert c.locator["thread_ts"] == "T"
        assert c.locator["chunk_index"] == sub_i
        assert isinstance(c.locator["msg_range"], list) and len(c.locator["msg_range"]) == 2


async def test_no_text_fields_yields_only_end_of_task(tmp_path):
    plugin = FakePlugin(records={"/general": [{"text": "hi", "thread_ts": "A"}]})
    ctx = build_ctx(artifacts=FakeArtifactStore(tmp_path))
    items = await collect(MessageStreamProducer(ctx), _task(plugin, ocfg=ObjectConfig()))
    assert len(items) == 1 and isinstance(items[0], EndOfTask)


async def test_missing_object_records_none_ends_cleanly(tmp_path):
    plugin = FakePlugin(records={})  # read_records returns None
    ctx = build_ctx(artifacts=FakeArtifactStore(tmp_path))
    items = await collect(MessageStreamProducer(ctx), _task(plugin))
    assert len(items) == 1 and isinstance(items[0], EndOfTask)
