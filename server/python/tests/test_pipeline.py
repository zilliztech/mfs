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
    cb1.assert_awaited_once_with("c://x/T", "job1", 1, False, None)  # 1 chunk, not partial, no err
    cb2.assert_awaited_once_with("c://x/T", "job1", 1, False, None)


async def test_zero_chunk_task_finalizes_and_still_deletes():
    c, embedder, milvus, tx = _consumer(batch_size=5)
    cb = AsyncMock()
    c.register_on_succeeded(cb)
    q = make_chunks_q(5)
    c.start(q)
    await q.put(_eot_env("EMPTY"))  # no chunks at all (e.g. emptied document)
    await c.shutdown()
    cb.assert_awaited_once_with("c://x/EMPTY", "job1", 0, False, None)  # zero chunks, no err
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


async def test_decimal_metadata_value_is_coerced_to_a_json_safe_type():
    # A Postgres NUMERIC/MONEY column deserializes to decimal.Decimal, which json
    # has no native representation for -- Milvus's upsert doesn't reject just that
    # field, it fails the WHOLE flush batch. _build_row must sanitize metadata
    # before a chunk ever reaches that call.
    from decimal import Decimal

    c, embedder, milvus, tx = _consumer(batch_size=1)
    q = make_chunks_q(1)
    c.start(q)
    env = _chunk_env("T", "a")
    env.payload.metadata = {"amount": Decimal("129.00"), "status": "pending"}
    await q.put(env)
    await c.shutdown()
    row = milvus.upsert.call_args.args[0][0]
    assert row["metadata"]["amount"] == "129.00"
    assert row["metadata"]["status"] == "pending"
    milvus.upsert.assert_awaited_once()  # the batch actually reached Milvus, not dropped as failed


async def test_success_callback_carries_chunk_count_and_partial():
    # the success hook receives (task_uri, job_id, chunk_count, partial, error); chunk_count is
    # cumulative across flushes and partial ORs every chunk's + the EndOfTask's flag.
    c, embedder, milvus, tx = _consumer(batch_size=2)  # forces T1 across two flushes
    seen: dict[str, tuple] = {}
    c.register_on_succeeded(
        lambda uri, job, count, partial, error=None: seen.update({uri: (count, partial)})
    )
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

    def boom(uri, job, count, partial, error=None):
        order.append(f"boom:{uri}")
        raise RuntimeError("callback DB hiccup")

    def ok(uri, job, count, partial, error=None):
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


async def test_embed_failure_drops_batch_and_finalizes_failed_without_wedge():
    # A failing embed must NOT wedge the consumer: the batch is dropped, the affected task's
    # pending is released, it finalizes failed (partial=True + error), and the run() loop keeps
    # draining the NEXT task. No retry, no error-string inspection.
    # batch_size=1 makes each chunk flush as it is consumed, so ordering is deterministic:
    # T1's flush fails before T2 is ever embedded.
    c, embedder, milvus, tx = _consumer(batch_size=1)
    finals: list[tuple] = []
    c.register_on_succeeded(
        lambda uri, job, count, partial, error: finals.append((uri, partial, error))
    )

    boom = {"armed": True}

    async def flaky_embed(texts):
        if boom["armed"]:
            boom["armed"] = False  # only the FIRST embed call (T1) blows up
            raise MemoryError("simulated 118 GB allocation")
        return [[float(i)] for i in range(len(texts))]

    embedder.batch_embed = AsyncMock(side_effect=flaky_embed)
    q = make_chunks_q(1)
    c.start(q)

    # T1: embed raises on its flush -> batch dropped, T1 finalized failed on its EndOfTask.
    await q.put(_chunk_env("T1", "a"))
    await q.put(_eot_env("T1"))
    # T2: processed after T1 failed -> proves the consumer is still alive and draining.
    await q.put(_chunk_env("T2", "b"))
    await q.put(_eot_env("T2"))
    await c.shutdown()

    by_uri = {u: (partial, error) for (u, partial, error) in finals}
    assert by_uri["c://x/T1"][0] is True  # failed task flagged partial
    assert "MemoryError" in by_uri["c://x/T1"][1]  # raw error propagated (type name only)
    assert by_uri["c://x/T2"] == (False, None)  # next task processed normally, no error
    assert c._pending == {} and c._task_errors == {}  # all per-task state released
    milvus.upsert.assert_awaited_once()  # only T2 was written; the failed T1 batch was dropped


async def test_upsert_failure_drops_batch_and_finalizes_failed():
    # Symmetry with the embed-failure path: a failing milvus.upsert also drops the batch and
    # finalizes the affected task failed rather than wedging.
    c, embedder, milvus, tx = _consumer(batch_size=10, idle_ms=30)
    finals: list[tuple] = []
    c.register_on_succeeded(
        lambda uri, job, count, partial, error: finals.append((uri, count, partial, error))
    )
    milvus.upsert = AsyncMock(side_effect=RuntimeError("milvus write error"))
    q = make_chunks_q(10)
    c.start(q)

    await q.put(_chunk_env("T", "a"))
    await q.put(_chunk_env("T", "b"))
    await q.put(_eot_env("T"))
    await c.shutdown()

    assert len(finals) == 1
    uri, count, partial, error = finals[0]
    # Bug C: chunk_count must reflect what actually persisted (nothing — the whole batch was
    # dropped), never the attempted count.
    assert uri == "c://x/T" and count == 0 and partial is True and "RuntimeError" in error
    assert c._pending == {} and c._task_errors == {}


async def test_duplicate_chunk_id_within_batch_dedupes_and_both_tasks_succeed():
    # Bug B: two chunks (from different tasks, but landing in the same flush) that hash to the
    # same chunk_id must NOT crash the whole batch — dedupe by chunk_id (last-write-wins) before
    # upsert, and both tasks still finalize successfully with the deduped count.
    class _DedupingConsumer(EmbedConsumer):
        def _build_row(self, env, chunk, vec):
            row = super()._build_row(env, chunk, vec)
            row["chunk_id"] = f"cid-{chunk.locator}"  # deliberately collide via shared locator
            return row

    embedder, milvus, tx = _mocks()
    c = _DedupingConsumer(embedder, milvus, tx, batch_size=2, idle_ms=5000)
    finals: dict[str, tuple] = {}
    c.register_on_succeeded(
        lambda uri, job, count, partial, error=None: finals.update({uri: (count, partial, error)})
    )
    q = make_chunks_q(2)
    c.start(q)

    # A and B each contribute one chunk with the SAME locator -> same chunk_id, same flush.
    await q.put(_chunk_env("A", "content-a", locator="shared"))
    await q.put(_chunk_env("B", "content-b", locator="shared"))  # triggers flush at batch_size=2
    await q.put(_eot_env("A"))
    await q.put(_eot_env("B"))
    await c.shutdown()

    assert milvus.upsert.call_count == 1
    rows = milvus.upsert.call_args.args[0]
    assert len(rows) == 1  # deduped down to one row
    assert rows[0]["content"] == "content-b"  # last occurrence wins (B was appended after A)
    assert rows[0]["chunk_id"] == "cid-shared"

    # both tasks finalize successfully (no error), not failed — the batch was never dropped.
    assert finals["c://x/A"][2] is None
    assert finals["c://x/B"][2] is None
    # only the single surviving (deduped) row is credited, and it was B's (last-write-wins).
    assert finals["c://x/A"] == (0, False, None)
    assert finals["c://x/B"] == (1, False, None)


async def test_partial_success_then_failure_reports_only_successful_chunks():
    # The most important Bug C case: a task spanning two flushes where the first succeeds (2
    # chunks written) and the second fails (1 more chunk attempted) must finalize with
    # chunk_count == 2 (only what actually persisted), not 3 (the attempted total).
    c, embedder, milvus, tx = _consumer(batch_size=2, idle_ms=5000)
    finals: dict[str, tuple] = {}
    c.register_on_succeeded(
        lambda uri, job, count, partial, error=None: finals.update({uri: (count, partial, error)})
    )

    upsert_calls = {"n": 0}

    async def flaky_upsert(rows):
        upsert_calls["n"] += 1
        if upsert_calls["n"] == 2:
            raise RuntimeError("milvus write error")

    milvus.upsert = AsyncMock(side_effect=flaky_upsert)
    q = make_chunks_q(2)
    c.start(q)

    # first flush (2 chunks) succeeds.
    await q.put(_chunk_env("T", "a"))
    await q.put(_chunk_env("T", "b"))
    # second flush (1 more chunk, below batch_size) fails via the idle-timeout drain.
    await q.put(_chunk_env("T", "c"))
    await q.put(_eot_env("T"))
    await c.shutdown()

    assert upsert_calls["n"] == 2
    count, partial, error = finals["c://x/T"]
    assert count == 2  # only the first, successful flush's chunks
    assert partial is True  # flagged partial by the dropped second batch
    assert error is not None and "RuntimeError" in error


async def test_on_task_retry_resets_per_task_state():
    # findings (8)/(9): a producer that raised after pumping 2 chunks (no EndOfTask) left
    # _deleted/_pending/_count behind. on_task_retry must clear them so the retry re-deletes
    # and counts only its own chunks.
    c, embedder, milvus, tx = _consumer(batch_size=10, idle_ms=30)
    seen: dict[str, tuple] = {}
    c.register_on_succeeded(
        lambda uri, job, count, partial, error=None: seen.update({uri: (count, partial)})
    )
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


async def test_mark_job_cancelled_skips_future_flush_without_embedding():
    # `mfs job cancel` can't interrupt a batch_embed() call already in flight, but the NEXT
    # flush for that job's chunks should skip the embed/upsert entirely (via the same
    # _fail_batch path a real embed/Milvus failure uses) instead of burning embed time on
    # work that will never be written -- and finalize the task failed, not wedge it.
    c, embedder, milvus, tx = _consumer(batch_size=10, idle_ms=30)
    finals: dict[str, tuple] = {}
    c.register_on_succeeded(
        lambda uri, job, count, partial, error: finals.update({uri: (count, partial, error)})
    )
    q = make_chunks_q(10)
    c.start(q)

    c.mark_job_cancelled("job1")  # T's job (default job="job1" in _chunk_env/_eot_env)
    await q.put(_chunk_env("T", "a"))
    await q.put(_chunk_env("T", "b"))
    await q.put(_eot_env("T"))
    await c.shutdown()

    embedder.batch_embed.assert_not_awaited()  # never even tried to embed a cancelled job's chunks
    milvus.upsert.assert_not_awaited()
    count, partial, error = finals["c://x/T"]
    assert count == 0 and partial is True and "job_cancelled" in error
    assert c._pending == {} and c._task_errors == {}  # released, not leaked


async def test_mark_job_cancelled_only_affects_that_job_not_others_in_same_batch():
    # Two tasks from different jobs land in the SAME flush; cancelling one job must not
    # affect the other's chunks, which should embed/upsert/finalize normally.
    c, embedder, milvus, tx = _consumer(batch_size=10, idle_ms=30)
    finals: dict[str, tuple] = {}
    c.register_on_succeeded(
        lambda uri, job, count, partial, error: finals.update({uri: (job, count, partial, error)})
    )
    q = make_chunks_q(10)
    c.start(q)

    c.mark_job_cancelled("job-cancelled")
    await q.put(_chunk_env("T1", "a", job="job-cancelled"))
    await q.put(_eot_env("T1", job="job-cancelled"))
    await q.put(_chunk_env("T2", "b", job="job-active"))
    await q.put(_eot_env("T2", job="job-active"))
    await c.shutdown()

    assert finals["c://x/T1"] == ("job-cancelled", 0, True, "RuntimeError: job_cancelled")
    assert finals["c://x/T2"] == ("job-active", 1, False, None)
    milvus.upsert.assert_awaited_once()  # only T2's row was ever written
    (written_rows,), _ = milvus.upsert.call_args
    assert len(written_rows) == 1  # T1's chunk never reached the embed/upsert call at all
