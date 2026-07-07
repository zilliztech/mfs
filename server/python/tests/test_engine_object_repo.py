"""ObjectRepository + state machine unit tests.

Covers the four-table SQL repository (objects / object_tasks / connector_jobs /
connectors) and the task/job state machine that consolidates the status transitions
previously scattered across engine.py. Pure unit tests against an in-memory sqlite
metadata store (no Milvus / embedding), plus pure-data assertions over the transition
tables (no DB).
"""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mfs_server.config import ServerConfig
from mfs_server.connectors.base import PathStat
from mfs_server.engine.components.object_repository import (
    JobStatus,
    TaskStatus,
    _JOB_TRANSITIONS,
    _TASK_TRANSITIONS,
)
from mfs_server.engine.engine import Engine

# The full cartesian of (from, to) task status pairs; used to partition legal vs
# illegal transitions exhaustively.
_ALL_TASK_STATUSES = list(TaskStatus)
_ALL_JOB_STATUSES = list(JobStatus)

# All tests in this file share ONE sqlite db file (one meta.db / tx.db pair for the
# whole run) — not one per test. The shared path is created once per session under a
# fixed temp dir; each test gets its own Engine/connection into that same file (so the
# aiosqlite worker thread is closed per-test and pytest exits cleanly), and each test
# wipes the seeded rows in setup so it starts from a clean schema.
_SHARED_DIR = Path(tempfile.mkdtemp(prefix="mfs-engine-object-repo"))
_SHARED_META = _SHARED_DIR / "meta.db"
_SHARED_TX = _SHARED_DIR / "tx.db"
_SHARED_ART = _SHARED_DIR / "art"

# Tables any test in this file can seed; wiped in setup so each test starts clean
# against the shared db. foreign_keys is OFF (set at build time), so order is free.
_RESET_TABLES = (
    "object_tasks",
    "connector_jobs",
    "objects",
    "connectors",
    "connector_state",
    "file_state",
    "watch_grants",
    "artifact_cache",
)

# Engines built this test, closed in teardown so the aiosqlite worker thread joins and
# the process doesn't hang after the run.
_ENGINES: list[Engine] = []


@pytest.fixture(autouse=True)
async def _reset_and_close():
    """Wipe seeded rows before each test (clean schema) and close every Engine built
    during it afterward (clean process exit)."""
    yield
    while _ENGINES:
        eng = _ENGINES.pop()
        try:
            await eng.infra.meta.close()
        except Exception:  # noqa: BLE001 — teardown must never mask a test failure
            pass


async def _build_engine(tmp_path=None) -> Engine:
    """Build a fresh Engine/connection into the SESSION-SHARED db file. `tmp_path` is
    accepted for call-site compatibility but unused — the shared path is fixed at
    import time. Schema is created idempotently (IF NOT EXISTS), so the first call
    creates it and later calls are no-ops on that front."""
    cfg = ServerConfig()
    cfg.metadata.backend = "sqlite"
    cfg.metadata.path = str(_SHARED_META)
    cfg.transformation_cache.backend = "sqlite"
    cfg.transformation_cache.db_path = str(_SHARED_TX)
    cfg.artifact_cache.root = str(_SHARED_ART)
    eng = Engine(cfg)
    await eng.infra.meta.connect()
    await eng.infra.meta.init_schema()
    await eng.infra.meta.execute("PRAGMA foreign_keys=OFF")  # seed rows without parent FKs
    # Wipe residue from a prior test: the shared file persists across tests in the run.
    for tbl in _RESET_TABLES:
        await eng.infra.meta.execute(f"DELETE FROM {tbl}")
    _ENGINES.append(eng)
    return eng


def _stat(rel: str) -> PathStat:
    return PathStat(
        path=rel,
        type="file",
        media_type="text/markdown",
        size_hint=10,
        fingerprint="fp:" + rel,
    )


async def _seed_connector(eng, *, cid="cA", status="active", root_uri="file:///repo"):
    await eng.infra.meta.execute(
        "INSERT INTO connectors (id, namespace_id, root_uri, type, status, config_json, registered_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (cid, eng.ns, root_uri, "file", status, "{}", datetime.now(timezone.utc).isoformat()),
    )


async def _seed_job(eng, *, job_id, cid, status="running", heartbeat=None):
    await eng.infra.meta.execute(
        "INSERT INTO connector_jobs (id, namespace_id, connector_id, op_kind, trigger, status, "
        " started_at, heartbeat) VALUES (?,?,?,?,?,?,?,?)",
        (
            job_id,
            eng.ns,
            cid,
            "sync",
            "manual",
            status,
            datetime.now(timezone.utc).isoformat(),
            heartbeat,
        ),
    )


async def _seed_task(
    eng,
    *,
    task_id,
    job_id,
    cid,
    object_uri="/a.md",
    status="pending",
    change_kind="added",
    attempts=0,
    priority=0,
):
    await eng.infra.meta.execute(
        "INSERT INTO object_tasks (id, connector_job_id, connector_id, object_uri, old_uri, "
        " change_kind, status, priority, attempts) VALUES (?,?,?,?,?,?,?,?,?)",
        (task_id, job_id, cid, object_uri, None, change_kind, status, priority, attempts),
    )


async def _task_status(eng, task_id) -> str | None:
    row = await eng.infra.meta.fetchone(
        "SELECT status, last_error FROM object_tasks WHERE id=?", (task_id,)
    )
    return row


# ----------------------------------------------------------------------
# state machine — transition tables (pure data, no DB)
# ----------------------------------------------------------------------


def test_task_transitions_table_is_exactly_the_documented_set():
    """The legal task transitions must be the single source of truth enumerated here."""
    assert _TASK_TRANSITIONS == frozenset(
        {
            (TaskStatus.PENDING, TaskStatus.RUNNING),
            (TaskStatus.PENDING, TaskStatus.CANCELLED),
            (TaskStatus.PENDING, TaskStatus.PENDING),
            (TaskStatus.RUNNING, TaskStatus.SUCCEEDED),
            (TaskStatus.RUNNING, TaskStatus.FAILED),
            (TaskStatus.RUNNING, TaskStatus.SKIPPED),
            (TaskStatus.RUNNING, TaskStatus.CANCELLED),
            (TaskStatus.RUNNING, TaskStatus.PENDING),
            (TaskStatus.FAILED, TaskStatus.PENDING),
        }
    )


def test_job_transitions_table_is_exactly_the_documented_set():
    assert _JOB_TRANSITIONS == frozenset(
        {
            (JobStatus.PREPARING, JobStatus.QUEUED),
            (JobStatus.PREPARING, JobStatus.FAILED),
            (JobStatus.PREPARING, JobStatus.CANCELLED),
            (JobStatus.QUEUED, JobStatus.RUNNING),
            (JobStatus.QUEUED, JobStatus.FAILED),
            (JobStatus.QUEUED, JobStatus.CANCELLED),
            (JobStatus.RUNNING, JobStatus.SUCCEEDED),
            (JobStatus.RUNNING, JobStatus.FAILED),
            (JobStatus.RUNNING, JobStatus.CANCELLED),
            (JobStatus.RUNNING, JobStatus.QUEUED),
        }
    )


@pytest.mark.parametrize("frm", _ALL_TASK_STATUSES)
@pytest.mark.parametrize("to", _ALL_TASK_STATUSES)
async def test_advance_task_rejects_every_illegal_task_transition(tmp_path, frm, to):
    """Every transition NOT in _TASK_TRANSITIONS must raise ValueError before touching the
    DB — no silent dirty writes. The guard runs before the first await, so a built (empty)
    engine suffices and no rows are seeded/modified for illegal pairs."""
    if (frm, to) in _TASK_TRANSITIONS:
        pytest.skip("legal transition — covered by DB tests below")
    eng = await _build_engine(tmp_path)
    with pytest.raises(ValueError, match="illegal task transition"):
        await eng.objects.advance_task("t", to, from_status=frm)


async def test_advance_task_self_transition_pending_pending_is_legal_guard_only(tmp_path):
    """(PENDING, PENDING) is legal at the guard; against an empty table the UPDATE matches
    nothing -> won == 0 (no raise). Confirms the guard does not reject this legal pair."""
    eng = await _build_engine(tmp_path)
    won = await eng.objects.advance_task("t", TaskStatus.PENDING, from_status=TaskStatus.PENDING)
    assert won == 0


# ----------------------------------------------------------------------
# advance_task — guarded semantics (sqlite)
# ----------------------------------------------------------------------


async def test_advance_task_succeeded_wins_when_running(tmp_path):
    eng = await _build_engine(tmp_path)
    await _seed_connector(eng)
    tid = uuid.uuid4().hex
    await _seed_task(eng, task_id=tid, job_id="j1", cid="cA", status="running")
    won = await eng.objects.advance_task(tid, TaskStatus.SUCCEEDED, from_status=TaskStatus.RUNNING)
    assert won == 1
    row = await _task_status(eng, tid)
    assert row["status"] == "succeeded"
    assert row["last_error"] is None


async def test_advance_task_returns_zero_when_concurrently_cancelled(tmp_path):
    """The 'won == 0' path: a task flipped off 'running' (cancel/remove racing the shared
    consumer) must NOT be revived. This is the trigger for orphan-chunk cleanup in
    _on_pipeline_object_indexed."""
    eng = await _build_engine(tmp_path)
    await _seed_connector(eng)
    tid = uuid.uuid4().hex
    await _seed_task(eng, task_id=tid, job_id="j1", cid="cA", status="cancelled")
    won = await eng.objects.advance_task(tid, TaskStatus.SUCCEEDED, from_status=TaskStatus.RUNNING)
    assert won == 0
    row = await _task_status(eng, tid)
    assert row["status"] == "cancelled"  # untouched


async def test_advance_task_failed_records_truncated_error(tmp_path):
    eng = await _build_engine(tmp_path)
    await _seed_connector(eng)
    tid = uuid.uuid4().hex
    await _seed_task(eng, task_id=tid, job_id="j1", cid="cA", status="running")
    long_err = "x" * 500
    won = await eng.objects.advance_task(
        tid, TaskStatus.FAILED, from_status=TaskStatus.RUNNING, error=long_err
    )
    assert won == 1
    row = await _task_status(eng, tid)
    assert row["status"] == "failed"
    assert row["last_error"] == long_err[:300]  # truncated to 300, matching engine.py


async def test_mark_task_failed_is_unguarded_overwrites_cancelled(tmp_path):
    """mark_task_failed preserves the original WHERE id=? (no status guard): a concurrently
    cancelled task is still overwritten — behavior equivalence with the pre-repo engine.py."""
    eng = await _build_engine(tmp_path)
    await _seed_connector(eng)
    tid = uuid.uuid4().hex
    await _seed_task(eng, task_id=tid, job_id="j1", cid="cA", status="cancelled")
    await eng.objects.mark_task_failed(tid, "boom")
    row = await _task_status(eng, tid)
    assert row["status"] == "failed"
    assert row["last_error"] == "boom"


async def test_mark_task_skipped_is_unguarded_overwrites_cancelled(tmp_path):
    eng = await _build_engine(tmp_path)
    await _seed_connector(eng)
    tid = uuid.uuid4().hex
    await _seed_task(eng, task_id=tid, job_id="j1", cid="cA", status="cancelled")
    await eng.objects.mark_task_skipped(tid, "gone")
    row = await _task_status(eng, tid)
    assert row["status"] == "skipped"


# ----------------------------------------------------------------------
# claim_tasks — conditional claim, no double-processing
# ----------------------------------------------------------------------


async def test_claim_tasks_takes_pending_only_priority_then_age(tmp_path):
    eng = await _build_engine(tmp_path)
    await _seed_connector(eng)
    # priority 5 (lower prio) and priority 1 (higher prio); claim orders by priority ASC
    await _seed_task(eng, task_id="t1", job_id="j1", cid="cA", object_uri="/lo.md", priority=5)
    await _seed_task(eng, task_id="t2", job_id="j1", cid="cA", object_uri="/hi.md", priority=1)
    await _seed_task(
        eng, task_id="t3", job_id="j1", cid="cA", object_uri="/done.md", status="succeeded"
    )
    claimed = await eng.objects.claim_tasks("cA", 64)
    assert {c["id"] for c in claimed} == {"t1", "t2"}
    # higher priority first
    assert claimed[0]["id"] == "t2"
    # all claimed rows are now running; a second claim gets nothing
    assert await eng.objects.claim_tasks("cA", 64) == []


async def test_claim_tasks_concurrent_calls_never_double_claim(tmp_path):
    """Two concurrent claim_tasks over the same pending rows: the conditional UPDATE
    (rowcount == 1) means each row is claimed by exactly one caller."""
    eng = await _build_engine(tmp_path)
    await _seed_connector(eng)
    for i in range(6):
        await _seed_task(eng, task_id=f"t{i}", job_id="j1", cid="cA", object_uri=f"/{i}.md")
    a, b = await asyncio.gather(
        eng.objects.claim_tasks("cA", 64), eng.objects.claim_tasks("cA", 64)
    )
    ids_a = {r["id"] for r in a}
    ids_b = {r["id"] for r in b}
    assert ids_a.isdisjoint(ids_b)  # no double-claim
    assert ids_a | ids_b == {f"t{i}" for i in range(6)}  # every row claimed exactly once


async def test_claim_tasks_excludes_dir_summary(tmp_path):
    eng = await _build_engine(tmp_path)
    await _seed_connector(eng)
    await _seed_task(eng, task_id="t1", job_id="j1", cid="cA", change_kind="added")
    await _seed_task(eng, task_id="t2", job_id="j1", cid="cA", change_kind="dir_summary")
    claimed = await eng.objects.claim_tasks("cA", 64)
    assert {c["id"] for c in claimed} == {"t1"}


# ----------------------------------------------------------------------
# open_sync_job — slot reservation + reopen
# ----------------------------------------------------------------------


async def test_open_sync_job_connector_removing_raises(tmp_path):
    eng = await _build_engine(tmp_path)
    await _seed_connector(eng, status="removing")
    with pytest.raises(ValueError, match="connector_removing"):
        await eng.objects.open_sync_job("cA", process=True)


async def test_open_sync_job_second_running_raises_sync_already_running(tmp_path):
    eng = await _build_engine(tmp_path)
    await _seed_connector(eng)
    await _seed_job(eng, job_id="j1", cid="cA", status="running")
    with pytest.raises(ValueError, match="sync_already_running"):
        await eng.objects.open_sync_job("cA", process=True)


async def test_open_sync_job_reopens_failed_and_pending_tasks(tmp_path):
    eng = await _build_engine(tmp_path)
    await _seed_connector(eng)
    # leftover tasks from a prior run: failed + pending get reopened; dir_summary does not
    await _seed_task(eng, task_id="tf", job_id=None, cid="cA", object_uri="/f.md", status="failed")
    await _seed_task(eng, task_id="tp", job_id=None, cid="cA", object_uri="/p.md", status="pending")
    await _seed_task(
        eng,
        task_id="td",
        job_id=None,
        cid="cA",
        object_uri="/d",
        status="failed",
        change_kind="dir_summary",
    )
    job_id = await eng.objects.open_sync_job("cA", process=True)
    assert job_id
    rows = await eng.infra.meta.fetchall(
        "SELECT id, connector_job_id, status, change_kind FROM object_tasks WHERE connector_id=?",
        ("cA",),
    )
    by_id = {r["id"]: r for r in rows}
    assert by_id["tf"]["connector_job_id"] == job_id and by_id["tf"]["status"] == "pending"
    assert by_id["tp"]["connector_job_id"] == job_id and by_id["tp"]["status"] == "pending"
    # dir_summary is NOT reopened: job_id untouched, status stays 'failed'
    assert by_id["td"]["connector_job_id"] is None and by_id["td"]["status"] == "failed"


# ----------------------------------------------------------------------
# finalize_job — terminal status selection + counts
# ----------------------------------------------------------------------


async def test_finalize_job_cancelled_wins_over_aborted(tmp_path):
    eng = await _build_engine(tmp_path)
    await _seed_connector(eng, cid="cC")
    await _seed_job(eng, job_id="j1", cid="cC", status="cancelled")
    status = await eng.objects.finalize_job("j1", "sync_error: boom")
    assert status == "cancelled"


async def test_finalize_job_aborted_means_failed(tmp_path):
    eng = await _build_engine(tmp_path)
    await _seed_connector(eng, cid="cF")
    await _seed_job(eng, job_id="j1", cid="cF", status="running")
    status = await eng.objects.finalize_job("j1", "sync_error: boom")
    assert status == "failed"


async def test_finalize_job_success_counts(tmp_path):
    eng = await _build_engine(tmp_path)
    await _seed_connector(eng, cid="cS")
    await _seed_job(eng, job_id="j1", cid="cS", status="running")
    await _seed_task(eng, task_id="t1", job_id="j1", cid="cS", status="succeeded")
    await _seed_task(eng, task_id="t2", job_id="j1", cid="cS", status="failed")
    await _seed_task(eng, task_id="t3", job_id="j1", cid="cS", status="cancelled")
    status = await eng.objects.finalize_job("j1", None)
    assert status == "succeeded"
    row = await eng.infra.meta.fetchone(
        "SELECT total_objects, succeeded_objects, failed_objects, cancelled_objects, error "
        "FROM connector_jobs WHERE id=?",
        ("j1",),
    )
    assert row["total_objects"] == 3
    assert row["succeeded_objects"] == 1
    assert row["failed_objects"] == 1
    assert row["cancelled_objects"] == 1
    assert row["error"] is None


# ----------------------------------------------------------------------
# write_object_row — UPSERT
# ----------------------------------------------------------------------


async def test_write_object_row_upsert_updates_in_place(tmp_path):
    eng = await _build_engine(tmp_path)
    await _seed_connector(eng)
    st = _stat("/a.md")
    await eng.objects.write_object_row("cA", "/a.md", st, True, "indexed", 7)
    await eng.objects.write_object_row("cA", "/a.md", st, False, "not_indexed", 0)
    rows = await eng.infra.meta.fetchall("SELECT * FROM objects WHERE connector_id=?", ("cA",))
    assert len(rows) == 1  # UPSERT, not a second row
    r = rows[0]
    assert r["search_status"] == "not_indexed"
    assert r["chunk_count"] == 0
    assert r["indexable"] == 0
    assert r["parent_path"] == "/"  # os.path.dirname('/a.md') or '/'


async def test_delete_object_row(tmp_path):
    eng = await _build_engine(tmp_path)
    await _seed_connector(eng)
    await eng.objects.write_object_row("cA", "/a.md", _stat("/a.md"), True, "indexed", 1)
    await eng.objects.delete_object_row("cA", "/a.md")
    assert (
        await eng.infra.meta.fetchone(
            "SELECT 1 FROM objects WHERE connector_id=? AND object_uri=?", ("cA", "/a.md")
        )
        is None
    )


# ----------------------------------------------------------------------
# delete_object_task_job_rows_for_connector — scope is exactly the three tables
# ----------------------------------------------------------------------


async def test_delete_three_tables_leaves_connector_and_state(tmp_path):
    eng = await _build_engine(tmp_path)
    await _seed_connector(eng)
    await _seed_job(eng, job_id="j1", cid="cA", status="succeeded")
    await _seed_task(eng, task_id="t1", job_id="j1", cid="cA")
    await eng.objects.write_object_row("cA", "/a.md", _stat("/a.md"), True, "indexed", 1)
    # out-of-scope tables must survive
    await eng.infra.meta.execute(
        "INSERT INTO connector_state (connector_id, key, value, updated_at) VALUES (?,?,?,?)",
        ("cA", "cursor", "v", datetime.now(timezone.utc).isoformat()),
    )
    await eng.infra.meta.execute(
        "INSERT INTO file_state (namespace_id, connector_id, path, status) VALUES (?,?,?,?)",
        (eng.ns, "cA", "/a.md", "indexed"),
    )
    await eng.objects.delete_object_task_job_rows_for_connector("cA")
    assert (
        await eng.infra.meta.fetchone("SELECT 1 FROM object_tasks WHERE connector_id=?", ("cA",))
        is None
    )
    assert (
        await eng.infra.meta.fetchone("SELECT 1 FROM connector_jobs WHERE connector_id=?", ("cA",))
        is None
    )
    assert (
        await eng.infra.meta.fetchone("SELECT 1 FROM objects WHERE connector_id=?", ("cA",)) is None
    )
    # connector row + connector_state + file_state untouched (caller-owned)
    assert await eng.infra.meta.fetchone("SELECT 1 FROM connectors WHERE id=?", ("cA",)) is not None
    assert (
        await eng.infra.meta.fetchone("SELECT 1 FROM connector_state WHERE connector_id=?", ("cA",))
        is not None
    )
    assert (
        await eng.infra.meta.fetchone("SELECT 1 FROM file_state WHERE connector_id=?", ("cA",))
        is not None
    )


# ----------------------------------------------------------------------
# objects reads — counts / summaries / not_indexed scope
# ----------------------------------------------------------------------


async def test_count_indexed_and_summaries(tmp_path):
    eng = await _build_engine(tmp_path)
    await _seed_connector(eng)
    for uri, ss, cc in [("/a.md", "indexed", 3), ("/b.md", "indexed", 1), ("/c.md", "partial", 0)]:
        await eng.objects.write_object_row("cA", uri, _stat(uri), True, ss, cc)
    assert await eng.objects.count_indexed_objects("cA") == 2
    by_status = {
        r["search_status"]: r["n"]
        for r in await eng.objects.summarize_objects_by_search_status("cA")
    }
    assert by_status == {"indexed": 2, "partial": 1}
    totals = await eng.objects.summarize_objects_totals("cA")
    assert totals["n"] == 3
    assert totals["chunks"] == 4


async def test_list_not_indexed_in_scope_root_branch(tmp_path):
    eng = await _build_engine(tmp_path)
    await _seed_connector(eng)
    for uri, ss in [("/a.md", "not_indexed"), ("/src/b.md", "not_indexed"), ("/ok.md", "indexed")]:
        await eng.objects.write_object_row("cA", uri, _stat(uri), True, ss, 0)
    rows = await eng.objects.list_not_indexed_in_scope("cA", "/")
    assert {r["object_uri"] for r in rows} == {"/a.md", "/src/b.md"}


async def test_list_not_indexed_in_scope_subpath_boundary(tmp_path):
    """scope '/src' matches '/src/...' but NOT a sibling '/src-old' (path-component boundary,
    LIKE wildcards escaped)."""
    eng = await _build_engine(tmp_path)
    await _seed_connector(eng)
    for uri in ["/src/a.md", "/src/sub/b.md", "/src-old/c.md", "/src_d.md"]:
        await eng.objects.write_object_row("cA", uri, _stat(uri), True, "not_indexed", 0)
    rows = await eng.objects.list_not_indexed_in_scope("cA", "/src")
    assert {r["object_uri"] for r in rows} == {"/src/a.md", "/src/sub/b.md"}


async def test_list_objects_with_chunks(tmp_path):
    eng = await _build_engine(tmp_path)
    await _seed_connector(eng)
    await eng.objects.write_object_row("cA", "/a.md", _stat("/a.md"), True, "indexed", 5)
    await eng.objects.write_object_row("cA", "/b.md", _stat("/b.md"), True, "not_indexed", 0)
    rows = await eng.objects.list_objects_with_chunks("cA")
    assert {r["object_uri"]: r["chunk_count"] for r in rows} == {"/a.md": 5}


# ----------------------------------------------------------------------
# connectors — read/write round-trip
# ----------------------------------------------------------------------


async def test_connector_insert_lookup_removing_status(tmp_path):
    eng = await _build_engine(tmp_path)
    await eng.objects.insert_connector("cX", "file:///x", "file", '{"root":"/x"}')
    assert await eng.objects.has_connector_uri("file:///x") is True
    assert await eng.objects.has_any_connector() is True
    row = await eng.objects.get_connector_row_by_uri("file:///x")
    assert row["id"] == "cX" and row["type"] == "file" and row["status"] == "active"
    assert await eng.objects.get_connector_id_by_uri("file:///x") == "cX"
    await eng.objects.set_connector_removing("cX")
    assert await eng.objects.get_connector_status("cX") == "removing"
    cfg = await eng.objects.get_connector_config("cX")
    assert cfg["config_json"] == '{"root":"/x"}'
    await eng.objects.update_connector_config("cX", '{"root":"/x2"}')
    assert (await eng.objects.get_connector_config("cX"))["config_json"] == '{"root":"/x2"}'
    await eng.objects.delete_connector("cX")
    assert await eng.objects.get_connector_status("cX") is None


async def test_connector_drift_count_indexed(tmp_path):
    """count_indexed_objects backs the --config drift WARNING in register_or_get_connector."""
    eng = await _build_engine(tmp_path)
    await eng.objects.insert_connector("cD", "file:///d", "file", "{}")
    await eng.objects.write_object_row("cD", "/a.md", _stat("/a.md"), True, "indexed", 2)
    await eng.objects.write_object_row("cD", "/b.md", _stat("/b.md"), True, "partial", 1)
    assert await eng.objects.count_indexed_objects("cD") == 1


# ----------------------------------------------------------------------
# connector_jobs — stale reclaim helpers
# ----------------------------------------------------------------------


async def test_stale_reclaim_helpers(tmp_path):
    eng = await _build_engine(tmp_path)
    await _seed_connector(eng)
    stale_hb = "2020-01-01T00:00:00+00:00"  # older than the cutoff below
    cutoff = "2021-01-01T00:00:00+00:00"  # mirrors _reclaim_stale_jobs' now - stale_after_s
    await _seed_job(eng, job_id="jp", cid="cA", status="preparing", heartbeat=stale_hb)
    await _seed_job(eng, job_id="jr", cid="cA", status="running", heartbeat=stale_hb)
    # the unique index ux_jobs_one_pending would block two preparing/queued; 'running' is
    # separate. Both stale listings return the right rows.
    prep = await eng.objects.list_stale_preparing_jobs(cutoff)
    assert {r["id"] for r in prep} == {"jp"}
    run = await eng.objects.list_stale_running_jobs(cutoff)
    assert {r["id"] for r in run} == {"jr"}
    await eng.objects.fail_stale_preparing_job("jp")
    assert await eng.objects.get_job_status("jp") == "failed"
    await eng.objects.requeue_stale_running_job("jr")
    assert await eng.objects.get_job_status("jr") == "queued"


async def test_claim_queued_job_is_atomic(tmp_path):
    eng = await _build_engine(tmp_path)
    await _seed_connector(eng)
    await _seed_job(eng, job_id="j1", cid="cA", status="queued")
    claimed = await eng.objects.claim_queued_job()
    assert claimed["id"] == "j1"
    assert await eng.objects.get_job_status("j1") == "running"
    # second claim finds nothing
    assert await eng.objects.claim_queued_job() is None
