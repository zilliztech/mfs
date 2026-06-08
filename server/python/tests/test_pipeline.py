"""Unit tests for engine/pipeline.py — chunks_q sizing + EmbedConsumer batching/atomicity.

All three injected interfaces (Embedder / MilvusSink / TxCacheLike) are AsyncMocks; no
live network, no real Milvus / embedder.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from mfs_server.engine.pipeline import (
    EmbedConsumer,
    TaskEnvelope,
    _CHUNKS_Q_MIN_MAXSIZE,
    _make_chunks_q_maxsize,
    make_chunks_q,
)
from mfs_server.engine.producers.base import Chunk, EndOfTask


# --- helpers ---


def _chunk_env(
    task_id, content="x", *, connector_uri="c://x", job="job1", kind="body", locator=None
):
    uri = f"{connector_uri}/{task_id}"
    ch = Chunk(content=content, chunk_kind=kind, locator=locator, uri=uri, connector_job_id=job)
    return TaskEnvelope(
        task_id=task_id, task_uri=uri, connector_uri=connector_uri, job_id=job, payload=ch
    )


def _eot_env(task_id, *, connector_uri="c://x", job="job1", partial=False):
    uri = f"{connector_uri}/{task_id}"
    return TaskEnvelope(
        task_id=task_id,
        task_uri=uri,
        connector_uri=connector_uri,
        job_id=job,
        payload=EndOfTask(partial=partial),
    )


def _mocks(*, hit=False):
    embedder = AsyncMock()
    embedder.batch_embed = AsyncMock(
        side_effect=lambda texts: [[float(i)] for i in range(len(texts))]
    )
    milvus = AsyncMock()
    milvus.upsert = AsyncMock()
    milvus.delete_by_object = AsyncMock()
    tx = AsyncMock()
    if hit:
        tx.batch_get = AsyncMock(side_effect=lambda keys: {k: [9.9] for k in keys})
    else:
        tx.batch_get = AsyncMock(side_effect=lambda keys: {})
    tx.batch_put = AsyncMock()
    return embedder, milvus, tx


def _consumer(batch_size=3, idle_ms=5000, hit=False):
    embedder, milvus, tx = _mocks(hit=hit)
    c = EmbedConsumer(embedder, milvus, tx, batch_size=batch_size, idle_ms=idle_ms)
    return c, embedder, milvus, tx


# --- tests ---


def test_make_chunks_q_maxsize():
    assert _CHUNKS_Q_MIN_MAXSIZE == 200
    assert _make_chunks_q_maxsize(50) == 200  # 100 < floor
    assert _make_chunks_q_maxsize(100) == 200  # exactly floor
    assert _make_chunks_q_maxsize(150) == 300  # 2x above floor
    assert _make_chunks_q_maxsize(500) == 1000
    q = make_chunks_q(150)
    assert isinstance(q, asyncio.Queue) and q.maxsize == 300


async def test_batches_at_batch_size_threshold():
    c, embedder, milvus, tx = _consumer(batch_size=3)
    q = make_chunks_q(3)
    c.start(q)
    for i in range(3):
        await q.put(_chunk_env("T", f"chunk{i}"))
    await c.shutdown()
    # one flush fired at the 3rd chunk; shutdown's drain flush had an empty batch
    assert milvus.upsert.call_count == 1
    rows = milvus.upsert.call_args.args[0]
    assert len(rows) == 3


async def test_flushes_on_idle_timeout():
    c, embedder, milvus, tx = _consumer(batch_size=10, idle_ms=50)
    q = make_chunks_q(10)
    c.start(q)
    await q.put(_chunk_env("T", "a"))
    await q.put(_chunk_env("T", "b"))
    await asyncio.sleep(0.2)  # exceed idle_ms with the batch below batch_size
    assert milvus.upsert.call_count == 1
    assert len(milvus.upsert.call_args.args[0]) == 2
    await c.shutdown()


async def test_pending_decrements_after_upsert_ack():
    c, embedder, milvus, tx = _consumer(batch_size=2)
    snapshots = {}

    async def record_upsert(rows):
        # at upsert time the chunks are written-but-not-yet-acked: pending still counts them
        snapshots["at_upsert"] = c._pending.get("T", 0)

    milvus.upsert = AsyncMock(side_effect=record_upsert)
    q = make_chunks_q(2)
    c.start(q)
    await q.put(_chunk_env("T", "a"))
    await q.put(_chunk_env("T", "b"))  # triggers flush at batch_size=2
    await c.shutdown()
    assert snapshots["at_upsert"] == 2  # not decremented until after the ack
    assert c._pending.get("T", 0) == 0  # decremented post-ack


async def test_end_of_task_zero_pending_invokes_all_callbacks():
    c, embedder, milvus, tx = _consumer(batch_size=5)
    cb1 = AsyncMock()
    cb2 = AsyncMock()
    c.register_on_succeeded(cb1)
    c.register_on_succeeded(cb2)
    q = make_chunks_q(5)
    c.start(q)
    await q.put(_chunk_env("T", "a"))
    await q.put(_eot_env("T"))  # pending still 1 (chunk unflushed); finalize waits for write
    await c.shutdown()  # drain flush writes the chunk -> pending 0 + eot seen -> success
    cb1.assert_awaited_once_with("c://x/T", "job1", 1, False)  # 1 chunk, not partial
    cb2.assert_awaited_once_with("c://x/T", "job1", 1, False)


async def test_zero_chunk_task_finalizes_and_still_deletes():
    c, embedder, milvus, tx = _consumer(batch_size=5)
    cb = AsyncMock()
    c.register_on_succeeded(cb)
    q = make_chunks_q(5)
    c.start(q)
    await q.put(_eot_env("EMPTY"))  # no chunks at all (e.g. emptied document)
    await c.shutdown()
    cb.assert_awaited_once_with("c://x/EMPTY", "job1", 0, False)  # zero chunks
    # a zero-chunk rebuild must still purge any prior index
    milvus.delete_by_object.assert_awaited_once_with("c://x", "c://x/EMPTY")
    embedder.batch_embed.assert_not_called()


async def test_delete_by_object_once_per_task_not_per_chunk():
    c, embedder, milvus, tx = _consumer(batch_size=2)
    q = make_chunks_q(2)
    c.start(q)
    for i in range(3):  # 3 chunks, same task, spanning two flushes
        await q.put(_chunk_env("T", f"c{i}"))
    await c.shutdown()
    milvus.delete_by_object.assert_awaited_once_with("c://x", "c://x/T")


async def test_tx_cache_hit_avoids_embedder():
    c, embedder, milvus, tx = _consumer(batch_size=2, hit=True)
    q = make_chunks_q(2)
    c.start(q)
    await q.put(_chunk_env("T", "a"))
    await q.put(_chunk_env("T", "b"))
    await c.shutdown()
    embedder.batch_embed.assert_not_called()
    tx.batch_put.assert_not_called()
    rows = milvus.upsert.call_args.args[0]
    assert all(r["dense_vec"] == [9.9] for r in rows)  # cached vectors used


async def test_shutdown_drains_pending_batch():
    c, embedder, milvus, tx = _consumer(batch_size=10)
    q = make_chunks_q(10)
    c.start(q)
    await q.put(_chunk_env("T", "a"))
    await q.put(_chunk_env("T", "b"))  # below batch_size, never auto-flushes
    await c.shutdown()
    assert milvus.upsert.call_count == 1
    assert len(milvus.upsert.call_args.args[0]) == 2


async def test_multiple_tasks_interleaved_pending_isolated():
    c, embedder, milvus, tx = _consumer(batch_size=4)
    seen = []
    c.register_on_succeeded(lambda uri, job, *a: seen.append((uri, job)))
    q = make_chunks_q(4)
    c.start(q)
    # interleave A and B chunks in one batch, then their EndOfTasks
    await q.put(_chunk_env("A", "a1"))
    await q.put(_chunk_env("B", "b1"))
    await q.put(_chunk_env("A", "a2"))
    await q.put(_chunk_env("B", "b2"))  # batch_size=4 -> flush; A & B each written
    await q.put(_eot_env("A"))
    await q.put(_eot_env("B"))
    await c.shutdown()

    # each object purged exactly once
    deleted = {call.args for call in milvus.delete_by_object.await_args_list}
    assert deleted == {("c://x", "c://x/A"), ("c://x", "c://x/B")}
    # one batched upsert of 4 mixed-task rows
    assert milvus.upsert.call_count == 1
    rows = milvus.upsert.call_args.args[0]
    assert len(rows) == 4
    # both tasks finalized independently, no leftover pending
    assert set(seen) == {("c://x/A", "job1"), ("c://x/B", "job1")}
    assert c._pending == {}


async def test_partial_flag_round_trips_into_row():
    c, embedder, milvus, tx = _consumer(batch_size=1)
    q = make_chunks_q(1)
    c.start(q)
    env = _chunk_env("T", "a")
    env.payload.partial = True
    await q.put(env)
    await c.shutdown()
    row = milvus.upsert.call_args.args[0][0]
    assert row["partial"] is True


async def test_success_callback_carries_chunk_count_and_partial():
    # the success hook receives (task_uri, job_id, chunk_count, partial); chunk_count is
    # cumulative across flushes and partial ORs every chunk's + the EndOfTask's flag.
    c, embedder, milvus, tx = _consumer(batch_size=2)  # forces T1 across two flushes
    seen: dict[str, tuple] = {}
    c.register_on_succeeded(lambda uri, job, count, partial: seen.update({uri: (count, partial)}))
    q = make_chunks_q(2)
    c.start(q)

    # T1: 3 chunks (spans flushes), none partial -> (3, False)
    for i in range(3):
        await q.put(_chunk_env("T1", f"c{i}"))
    await q.put(_eot_env("T1"))
    # T2: one chunk flagged partial, EndOfTask not partial -> (1, True) via the chunk flag
    env = _chunk_env("T2", "big")
    env.payload.partial = True
    await q.put(env)
    await q.put(_eot_env("T2", partial=False))
    # T3: one clean chunk but the EndOfTask is partial (chunk_max truncation) -> (1, True)
    await q.put(_chunk_env("T3", "x"))
    await q.put(_eot_env("T3", partial=True))

    await c.shutdown()
    assert seen["c://x/T1"] == (3, False)
    assert seen["c://x/T2"] == (1, True)
    assert seen["c://x/T3"] == (1, True)


async def test_raising_callback_does_not_skip_others_or_kill_consumer():
    # A registered success hook that raises must NOT stop the other hooks from firing, and the
    # consumer must keep draining later tasks (a dead consumer would wedge the whole pipeline).
    c, embedder, milvus, tx = _consumer(batch_size=1)
    order: list[str] = []

    def boom(uri, job, count, partial):
        order.append(f"boom:{uri}")
        raise RuntimeError("callback DB hiccup")

    def ok(uri, job, count, partial):
        order.append(f"ok:{uri}")

    c.register_on_succeeded(boom)
    c.register_on_succeeded(ok)
    q = make_chunks_q(1)
    c.start(q)

    # T1: the raising hook fires, then the second hook still runs.
    await q.put(_chunk_env("T1", "a"))
    await q.put(_eot_env("T1"))
    # T2: arrives AFTER T1's hook raised — proves the consumer is still alive and draining.
    await q.put(_chunk_env("T2", "b"))
    await q.put(_eot_env("T2"))
    await c.shutdown()

    assert order == ["boom:c://x/T1", "ok:c://x/T1", "boom:c://x/T2", "ok:c://x/T2"]
    assert c._pending == {}  # both tasks finalized + cleaned up despite the raising hook


async def test_upsert_failure_preserves_batch_then_succeeds_on_retry():
    # finding (4): a failed milvus.upsert must not drop chunks or pending bookkeeping. The
    # batch is retried on the next flush and nothing is lost.
    c, embedder, milvus, tx = _consumer(batch_size=10, idle_ms=30)
    calls = {"n": 0}

    async def flaky_upsert(rows):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("milvus transient error")
        flaky_upsert.last = rows

    milvus.upsert = AsyncMock(side_effect=flaky_upsert)
    seen: list[tuple] = []
    c.register_on_succeeded(lambda uri, job, count, partial: seen.append((uri, count)))
    q = make_chunks_q(10)
    c.start(q)

    await q.put(_chunk_env("T", "a"))
    await q.put(_chunk_env("T", "b"))
    await q.put(_eot_env("T"))
    # below batch_size, so the batch flushes on the idle timer: flush #1 raises (caught),
    # the batch is preserved, flush #2 (next idle tick) retries and succeeds.
    await asyncio.sleep(0.15)
    await c.shutdown()

    assert calls["n"] == 2  # first flush failed, retried once
    assert len(flaky_upsert.last) == 2  # both chunks survived the failed flush
    assert seen == [("c://x/T", 2)]  # task finalized exactly once with both chunks counted
    assert c._pending == {}


async def test_on_task_retry_resets_per_task_state():
    # findings (8)/(9): a producer that raised after pumping 2 chunks (no EndOfTask) left
    # _deleted/_pending/_count behind. on_task_retry must clear them so the retry re-deletes
    # and counts only its own chunks.
    c, embedder, milvus, tx = _consumer(batch_size=10, idle_ms=30)
    seen: dict[str, tuple] = {}
    c.register_on_succeeded(lambda uri, job, count, partial: seen.update({uri: (count, partial)}))
    q = make_chunks_q(10)
    c.start(q)

    # attempt 1: 2 chunks pumped, then the producer raised (no EndOfTask ever sent).
    await q.put(_chunk_env("T", "a1"))
    await q.put(_chunk_env("T", "a2"))
    await asyncio.sleep(0.08)  # let the idle flush write them (delete_by_object runs once)
    assert milvus.delete_by_object.await_count == 1

    # engine resets the task before re-pumping.
    c.on_task_retry("T")
    assert c._pending.get("T", 0) == 0 and c._count.get("T", 0) == 0 and "T" not in c._deleted

    # attempt 2: a clean run of 3 chunks + EndOfTask.
    for i in range(3):
        await q.put(_chunk_env("T", f"b{i}"))
    await q.put(_eot_env("T"))
    await c.shutdown()

    assert seen["c://x/T"] == (3, False)  # counts ONLY the retry's chunks, not 2+3
    assert milvus.delete_by_object.await_count == 2  # delete ran again on the retry's first chunk
    assert c._pending == {}
