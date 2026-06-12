"""Unit tests for ConnectorJobWatcher — completion / failure / cancel + reduce evict."""

from __future__ import annotations

import uuid

from mfs_server.config import ServerConfig
from mfs_server.engine.job_watcher import ConnectorJobWatcher
from mfs_server.storage.metadata import make_metadata_store


class _FakeReduce:
    """Stand-in JobLaneCoordinator: records evict_job calls; tracks 'active' jobs whose
    DirTree the watcher should evict on terminal status."""

    def __init__(self, active=(), done=True):
        self._active = list(active)
        self._done = done
        self.evicted: list[str] = []

    def is_done(self, job_id):
        return self._done

    def active_jobs(self):
        return list(self._active)

    def evict_job(self, job_id):
        self.evicted.append(job_id)
        if job_id in self._active:
            self._active.remove(job_id)


async def _meta(tmp_path):
    cfg = ServerConfig()
    cfg.metadata.backend = "sqlite"
    cfg.metadata.path = str(tmp_path / "meta.db")
    meta = make_metadata_store(cfg)
    await meta.connect()
    await meta.init_schema()
    await meta.execute("PRAGMA foreign_keys=OFF")
    return meta


async def _job(meta, *, status):
    jid = uuid.uuid4().hex
    cid = uuid.uuid4().hex  # distinct connector per job (ux_jobs_one_running is per-connector)
    await meta.execute(
        "INSERT INTO connector_jobs (id, namespace_id, connector_id, op_kind, trigger, status, started_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (jid, "default", cid, "sync", "manual", status, "2026-01-01T00:00:00"),
    )
    return jid


async def _task(meta, job_id, *, status):
    await meta.execute(
        "INSERT INTO object_tasks (id, connector_job_id, connector_id, object_uri, old_uri, "
        " change_kind, status, priority, attempts) VALUES (?,?,?,?,?,?,?,?,0)",
        (uuid.uuid4().hex, job_id, "c", f"/{uuid.uuid4().hex}.md", None, "added", status, 0),
    )


async def _status(meta, job_id):
    row = await meta.fetchone("SELECT status FROM connector_jobs WHERE id=?", (job_id,))
    return row["status"]


async def test_completion_marks_succeeded_and_evicts(tmp_path):
    meta = await _meta(tmp_path)
    a = await _job(meta, status="running")
    b = await _job(meta, status="running")
    for _ in range(2):
        await _task(meta, a, status="succeeded")
    await _task(meta, b, status="succeeded")
    await _task(meta, b, status="pending")  # B still has work

    reduce = _FakeReduce()
    watcher = ConnectorJobWatcher(meta, reduce)
    await watcher.tick()

    assert await _status(meta, a) == "succeeded"
    assert reduce.evicted == [a]
    assert await _status(meta, b) == "running"  # B not finalized (a task still pending)
    await meta.close()


async def test_cancel_cleans_pending_and_evicts(tmp_path):
    meta = await _meta(tmp_path)
    b = await _job(meta, status="cancelled")  # user cancel already flipped the status
    await _task(meta, b, status="pending")  # a straggler pending task to clean up
    await _task(meta, b, status="succeeded")

    reduce = _FakeReduce(active=[b])  # B's DirTree still held
    watcher = ConnectorJobWatcher(meta, reduce)
    await watcher.tick()

    assert await _status(meta, b) == "cancelled"
    rows = await meta.fetchall("SELECT status FROM object_tasks WHERE connector_job_id=?", (b,))
    assert sorted(r["status"] for r in rows) == ["cancelled", "succeeded"]  # pending -> cancelled
    assert reduce.evicted == [b]
    await meta.close()


async def test_watcher_does_not_trip_breaker_on_failures(tmp_path):
    # The watcher is reconciliation only — it does NOT fail jobs on a cumulative failed count.
    # The circuit breaker lives in _run_job_loop (consecutive failures). A running job with
    # failures AND still-pending work must be left running by the watcher.
    meta = await _meta(tmp_path)
    j = await _job(meta, status="running")
    await _task(meta, j, status="failed")
    await _task(meta, j, status="failed")
    await _task(meta, j, status="pending")  # job still has live work

    reduce = _FakeReduce()
    watcher = ConnectorJobWatcher(meta, reduce)
    await watcher.tick()

    assert await _status(meta, j) == "running"  # not failed by the watcher
    rows = await meta.fetchall("SELECT status FROM object_tasks WHERE connector_job_id=?", (j,))
    assert rows_count(rows, "pending") == 1  # pending NOT cancelled by the watcher
    assert reduce.evicted == []
    await meta.close()


async def test_all_tasks_terminal_with_failures_completes(tmp_path):
    # When every task is terminal (some failed, none live) the job is a normal completion ->
    # 'succeeded' (partial-success; failed_objects is recorded by _finalize_job, not here).
    meta = await _meta(tmp_path)
    j = await _job(meta, status="running")
    await _task(meta, j, status="failed")
    await _task(meta, j, status="succeeded")

    reduce = _FakeReduce()
    watcher = ConnectorJobWatcher(meta, reduce)
    await watcher.tick()

    assert await _status(meta, j) == "succeeded"
    assert reduce.evicted == [j]
    await meta.close()


def rows_count(rows, status):
    return sum(1 for r in rows if r["status"] == status)


async def test_idempotent_no_double_evict(tmp_path):
    meta = await _meta(tmp_path)
    a = await _job(meta, status="running")
    await _task(meta, a, status="succeeded")

    reduce = _FakeReduce()
    watcher = ConnectorJobWatcher(meta, reduce)
    await watcher.tick()
    await watcher.tick()  # second cycle must not re-finalize or re-evict

    assert await _status(meta, a) == "succeeded"
    assert reduce.evicted == [a]  # evicted exactly once
    await meta.close()


async def test_running_job_no_tasks_not_finalized(tmp_path):
    # a job mid-enumeration (created 'running', no tasks yet) must NOT be marked succeeded
    meta = await _meta(tmp_path)
    j = await _job(meta, status="running")
    reduce = _FakeReduce()
    watcher = ConnectorJobWatcher(meta, reduce)
    await watcher.tick()
    assert await _status(meta, j) == "running"
    assert reduce.evicted == []
    await meta.close()


async def test_completion_waits_for_reduce(tmp_path):
    # all tasks done but the reduce subsystem still has outstanding directory summaries
    meta = await _meta(tmp_path)
    j = await _job(meta, status="running")
    await _task(meta, j, status="succeeded")
    reduce = _FakeReduce(done=False)  # reduce not done yet
    watcher = ConnectorJobWatcher(meta, reduce)
    await watcher.tick()
    assert await _status(meta, j) == "running"  # held until reduce completes
    assert reduce.evicted == []
    await meta.close()
