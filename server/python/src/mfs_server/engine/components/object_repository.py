"""
``ObjectRepository`` + task/job/connector state machine.

Consolidates all SQL for the ``objects`` / ``object_tasks`` / ``connector_jobs`` /
``connectors`` tables and the status-transition guard (``advance_task``) so the
invariants previously expressed only in ``WHERE status=?`` guards and inline
comments live as a single queryable transition table.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskStatus(str, Enum):
    """Legal values for ``object_tasks.status`` (framework-fixed, see connectors/base.py)."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class JobStatus(str, Enum):
    """Legal values for ``connector_jobs.status``."""

    PREPARING = "preparing"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ConnectorStatus(str, Enum):
    """Legal values for ``connectors.status``."""

    ACTIVE = "active"
    REMOVING = "removing"


# Legal task transitions (everything else is forbidden). Enumerated from the existing
# object_tasks.status write sites in engine.py: claim (pending->running), reopen
# (pending/failed -> pending), inline/pipeline done (running->succeeded),
# retry-exhausted/embed-error (running->failed), source-gone (running->skipped),
# cancel/remove/breaker (pending|running -> cancelled), reclaim (running->pending).
# advance_task asserts against this table, making the implicit ``WHERE status=?``
# guard invariant explicit.
_TASK_TRANSITIONS: frozenset[tuple[TaskStatus, TaskStatus]] = frozenset(
    {
        (TaskStatus.PENDING, TaskStatus.RUNNING),  # claim
        (TaskStatus.PENDING, TaskStatus.CANCELLED),  # cancel / remove / breaker
        (TaskStatus.PENDING, TaskStatus.PENDING),  # reopen: pending/failed -> pending
        (TaskStatus.RUNNING, TaskStatus.SUCCEEDED),  # inline / pipeline done
        (TaskStatus.RUNNING, TaskStatus.FAILED),  # retry exhausted / embed error
        (TaskStatus.RUNNING, TaskStatus.SKIPPED),  # source gone
        (TaskStatus.RUNNING, TaskStatus.CANCELLED),  # cancel mid-run
        (TaskStatus.RUNNING, TaskStatus.PENDING),  # reclaim: dead worker's task -> pending
        (TaskStatus.FAILED, TaskStatus.PENDING),  # reopen: failed -> pending (retry)
    }
)

# Legal job transitions (documentation + exhaustive parametrized tests). Job terminal
# writes are mostly batch/conditional UPDATEs (cancel/fail/queue/reclaim) that keep the
# original guard semantics and are NOT forced through an advance_job; this table is the
# single expression of legality, exercised by the tests.
_JOB_TRANSITIONS: frozenset[tuple[JobStatus, JobStatus]] = frozenset(
    {
        (JobStatus.PREPARING, JobStatus.QUEUED),  # enumeration done -> expose to worker
        (JobStatus.PREPARING, JobStatus.FAILED),  # reclaim: enumeration abandoned
        (JobStatus.PREPARING, JobStatus.CANCELLED),  # remove
        (JobStatus.QUEUED, JobStatus.RUNNING),  # worker claim
        (JobStatus.QUEUED, JobStatus.FAILED),  # worker connect failure
        (JobStatus.QUEUED, JobStatus.CANCELLED),  # cancel / remove
        (JobStatus.RUNNING, JobStatus.SUCCEEDED),  # finalize
        (JobStatus.RUNNING, JobStatus.FAILED),  # finalize / reclaim / worker failure
        (JobStatus.RUNNING, JobStatus.CANCELLED),  # cancel / remove
        (JobStatus.RUNNING, JobStatus.QUEUED),  # reclaim: re-queue stale running
    }
)


class ObjectRepository:
    """SQL repository + state machine for the ``objects`` / ``object_tasks`` /
    ``connector_jobs`` / ``connectors`` tables.

    ``Engine`` delegates every ``self.meta.execute/fetchone/fetchall`` against these
    four tables to ``self.objects.xxx(...)``; SQL is migrated verbatim with zero
    behavior change. ``Engine`` keeps the non-SQL orchestration and the thin
    delegate methods called directly by tests.
    """

    def __init__(self, meta, cfg):
        self._meta = meta
        self._cfg = cfg
        self._ns = cfg.namespace

    # ------------------------------------------------------------------
    # state machine — guarded per-task terminal transitions
    # ------------------------------------------------------------------
    async def advance_task(
        self,
        task_id: str,
        to: TaskStatus,
        *,
        error: str | None = None,
        from_status: TaskStatus,
    ) -> int:
        """Guarded per-task status transition.

        Asserts ``(from_status, to)`` is legal per ``_TASK_TRANSITIONS`` (illegal →
        raise, never silently write dirty), then issues a conditional UPDATE guarded
        on ``status=from_status`` and returns the rowcount (``won``). ``won == 0``
        means a concurrent cancel/remove beat us — the caller reconciles (e.g. delete
        orphan chunks in ``_on_pipeline_object_indexed``). Preserves the original
        ``WHERE id=? AND status='running'`` guard + ``won`` semantics (§4.4 / §5).

        Only used on the paths that originally carried a ``status='running'`` guard
        (pipeline/inline succeeded, pipeline failed). Unguarded terminal writes
        (``mark_task_failed`` / ``mark_task_skipped``) keep the original ``WHERE id=?``
        no-guard form to preserve behavior exactly.
        """
        if (from_status, to) not in _TASK_TRANSITIONS:
            raise ValueError(f"illegal task transition: {from_status.value} -> {to.value}")
        sets = "status=?, finished_at=?"
        params: list = [to.value, _now()]
        if error is not None:
            sets += ", last_error=?"
            params.append(str(error)[:300])
        params += [task_id, from_status.value]
        return await self._meta.execute_rowcount(
            f"UPDATE object_tasks SET {sets} WHERE id=? AND status=?",
            tuple(params),
        )

    # ------------------------------------------------------------------
    # object_tasks — writes
    # ------------------------------------------------------------------
    async def insert_task(
        self,
        task_id: str,
        job_id: str,
        cid: str,
        object_uri: str,
        old_uri: str | None,
        change_kind: str,
        priority: int,
    ) -> None:
        await self._meta.execute(
            "INSERT INTO object_tasks (id, connector_job_id, connector_id, object_uri, old_uri, "
            " change_kind, status, priority, attempts) VALUES (?,?,?,?,?,?,?,?,0)",
            (task_id, job_id, cid, object_uri, old_uri, change_kind, "pending", priority),
        )

    async def reclaim_tasks_for_reopen(self, job_id: str, cid: str, max_retries: int) -> None:
        """Re-attach a connector's leftover pending/failed tasks to a freshly opened sync job."""
        await self._meta.execute(
            "UPDATE object_tasks SET connector_job_id=?, status='pending' "
            "WHERE connector_id=? AND status IN ('pending','failed') AND attempts < ? "
            "AND change_kind != 'dir_summary'",
            (job_id, cid, max_retries),
        )

    async def cancel_pending_tasks_for_job(self, job_id: str) -> None:
        await self._meta.execute(
            "UPDATE object_tasks SET status='cancelled' "
            "WHERE connector_job_id=? AND status='pending'",
            (job_id,),
        )

    async def cancel_pending_running_tasks_for_job(self, job_id: str) -> None:
        await self._meta.execute(
            "UPDATE object_tasks SET status='cancelled' "
            "WHERE connector_job_id=? AND status IN ('pending','running')",
            (job_id,),
        )

    async def cancel_pending_tasks_for_connector(self, cid: str) -> None:
        await self._meta.execute(
            "UPDATE object_tasks SET status='cancelled' WHERE connector_id=? AND status='pending'",
            (cid,),
        )

    async def fail_running_tasks_for_job(self, job_id: str, error: str) -> None:
        await self._meta.execute(
            "UPDATE object_tasks SET status='failed', finished_at=?, last_error=? "
            "WHERE connector_job_id=? AND status='running'",
            (_now(), str(error)[:300], job_id),
        )

    async def reassign_running_tasks(self, to_job_id: str, from_job_id: str) -> None:
        """Hand a dead job's in-flight tasks to its in-flight sibling."""
        await self._meta.execute(
            "UPDATE object_tasks SET status='pending', connector_job_id=? "
            "WHERE connector_job_id=? AND status='running'",
            (to_job_id, from_job_id),
        )

    async def reset_running_tasks_to_pending(self, job_id: str) -> None:
        """Reset a dead worker's in-flight tasks back to pending before re-queuing the job."""
        await self._meta.execute(
            "UPDATE object_tasks SET status='pending' "
            "WHERE connector_job_id=? AND status='running'",
            (job_id,),
        )

    async def mark_task_skipped(self, task_id: str, error: str) -> None:
        """Unguarded terminal write (original ``WHERE id=?``, no status guard). Preserves
        the original semantics — a concurrently-cancelled task is still overwritten to
        'skipped'. Kept unguarded for behavior equivalence; not routed through
        ``advance_task``."""
        await self._meta.execute(
            "UPDATE object_tasks SET status='skipped', finished_at=?, last_error=? WHERE id=?",
            (_now(), error, task_id),
        )

    async def mark_task_failed(self, task_id: str, error: str) -> None:
        """Unguarded terminal write (original ``WHERE id=?``, no status guard). See
        ``mark_task_skipped``."""
        await self._meta.execute(
            "UPDATE object_tasks SET status='failed', finished_at=?, last_error=? WHERE id=?",
            (_now(), error, task_id),
        )

    # ------------------------------------------------------------------
    # object_tasks — reads
    # ------------------------------------------------------------------
    async def count_running_tasks(self, job_id: str) -> int:
        row = await self._meta.fetchone(
            "SELECT count(*) AS n FROM object_tasks WHERE connector_job_id=? AND status='running'",
            (job_id,),
        )
        return (row["n"] if row else 0) or 0

    async def list_job_tasks_excluding_dir_summary(self, job_id: str) -> list[dict]:
        return await self._meta.fetchall(
            "SELECT object_uri, status FROM object_tasks "
            "WHERE connector_job_id=? AND change_kind != 'dir_summary'",
            (job_id,),
        )

    async def claim_tasks(self, cid: str, limit: int) -> list[dict]:
        """Claim up to ``limit`` pending tasks for ONE connector (priority then age),
        taking each candidate with a conditional UPDATE guarded on status='pending'.
        Returns only the rows this worker actually flipped (rowcount == 1), so
        concurrent workers never double-process a task. Consolidates the original
        ``_claim_batch`` + ``_claim_rows``."""
        rows = await self._meta.fetchall(
            "SELECT * FROM object_tasks WHERE status='pending' AND connector_id=? "
            "AND change_kind != 'dir_summary' "
            "ORDER BY priority ASC, started_at ASC LIMIT ?",
            (cid, limit),
        )
        claimed = []
        for r in rows:
            won = await self._meta.execute_rowcount(
                "UPDATE object_tasks SET status='running', started_at=?, attempts=attempts+1 "
                "WHERE id=? AND status='pending'",
                (_now(), r["id"]),
            )
            if won == 1:
                claimed.append(r)
        return claimed

    # ------------------------------------------------------------------
    # connector_jobs — writes
    # ------------------------------------------------------------------
    async def open_sync_job(self, cid: str, process: bool) -> str:
        """Reserve the one-in-flight-sync slot for a connector and inherit its leftover
        tasks. Raises connector_removing / sync_already_running."""
        row = await self._meta.fetchone("SELECT status FROM connectors WHERE id=?", (cid,))
        if row and row["status"] == "removing":
            raise ValueError("connector_removing")
        job_id = uuid.uuid4().hex
        try:
            await self._meta.execute(
                "INSERT INTO connector_jobs (id, namespace_id, connector_id, op_kind, trigger, status, "
                " started_at, heartbeat) VALUES (?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    self._ns,
                    cid,
                    "sync",
                    "manual",
                    "running" if process else "preparing",
                    _now(),
                    _now(),
                ),
            )
        except Exception as e:  # noqa: BLE001 - unique-violation: one running/queued per connector
            if "unique" in str(e).lower() or "constraint" in str(e).lower():
                raise ValueError("sync_already_running") from e
            raise
        await self.reclaim_tasks_for_reopen(job_id, cid, self._cfg.object_task.max_retries)
        return job_id

    async def set_job_state_snapshot(self, job_id: str, snapshot: str) -> None:
        await self._meta.execute(
            "UPDATE connector_jobs SET state_snapshot=? WHERE id=?", (snapshot, job_id)
        )

    async def queue_preparing_job(self, job_id: str) -> None:
        await self._meta.execute(
            "UPDATE connector_jobs SET status='queued' WHERE id=? AND status='preparing'",
            (job_id,),
        )

    async def finalize_job(self, job_id: str, aborted: str | None) -> str:
        """Set terminal job status + per-status object counts. Returns the terminal
        status (so the caller can evict the Job Lane dir tree)."""
        counts = await self._meta.fetchall(
            "SELECT status, count(*) AS n FROM object_tasks WHERE connector_job_id=? GROUP BY status",
            (job_id,),
        )
        cmap = {r["status"]: r["n"] for r in counts}
        jrow = await self._meta.fetchone("SELECT status FROM connector_jobs WHERE id=?", (job_id,))
        if jrow and jrow["status"] == "cancelled":
            status = "cancelled"
        elif aborted:
            status = "failed"
        else:
            status = "succeeded"
        await self._meta.execute(
            "UPDATE connector_jobs SET status=?, finished_at=?, error=?, "
            " total_objects=?, succeeded_objects=?, failed_objects=?, cancelled_objects=? WHERE id=?",
            (
                status,
                _now(),
                aborted,
                sum(cmap.values()),
                cmap.get("succeeded", 0),
                cmap.get("failed", 0),
                cmap.get("cancelled", 0),
                job_id,
            ),
        )
        return status

    async def cancel_job_row(self, job_id: str) -> None:
        await self._meta.execute(
            "UPDATE connector_jobs SET status='cancelled', finished_at=? WHERE id=?",
            (_now(), job_id),
        )

    async def fail_inflight_job(self, job_id: str, error: str) -> None:
        await self._meta.execute(
            "UPDATE connector_jobs SET status='failed', finished_at=?, error=? "
            "WHERE id=? AND status IN ('running', 'queued')",
            (_now(), str(error)[:300], job_id),
        )

    async def fail_stale_preparing_job(self, job_id: str) -> None:
        await self._meta.execute(
            "UPDATE connector_jobs SET status='failed', finished_at=?, "
            "error='reclaimed: enumeration abandoned' WHERE id=? AND status='preparing'",
            (_now(), job_id),
        )

    async def fail_superseded_job(self, job_id: str) -> None:
        await self._meta.execute(
            "UPDATE connector_jobs SET status='failed', finished_at=?, "
            "error='reclaimed: superseded by in-flight job' WHERE id=? AND status='running'",
            (_now(), job_id),
        )

    async def requeue_stale_running_job(self, job_id: str) -> None:
        await self._meta.execute(
            "UPDATE connector_jobs SET status='queued' WHERE id=? AND status='running'",
            (job_id,),
        )

    async def cancel_queued_preparing_jobs(self, cid: str) -> None:
        await self._meta.execute(
            "UPDATE connector_jobs SET status='cancelled', finished_at=? "
            "WHERE connector_id=? AND status IN ('queued','preparing')",
            (_now(), cid),
        )

    async def cancel_running_job(self, job_id: str) -> None:
        await self._meta.execute(
            "UPDATE connector_jobs SET status='cancelled', finished_at=? "
            "WHERE id=? AND status='running'",
            (_now(), job_id),
        )

    async def refresh_heartbeat(self, job_id: str) -> None:
        await self._meta.execute(
            "UPDATE connector_jobs SET heartbeat=? WHERE id=?", (_now(), job_id)
        )

    # ------------------------------------------------------------------
    # connector_jobs — reads / claim
    # ------------------------------------------------------------------
    async def get_job_status(self, job_id: str) -> str | None:
        row = await self._meta.fetchone("SELECT status FROM connector_jobs WHERE id=?", (job_id,))
        return row["status"] if row else None

    async def get_job_state_and_status(self, job_id: str) -> dict | None:
        return await self._meta.fetchone(
            "SELECT state_snapshot, status FROM connector_jobs WHERE id=?", (job_id,)
        )

    async def claim_queued_job(self) -> dict | None:
        """Atomically claim the oldest queued job. Multi-worker safe: the claim is a
        conditional UPDATE guarded on status='queued'; returns the row only when this
        worker's UPDATE flipped it (rowcount == 1)."""
        candidates = await self._meta.fetchall(
            "SELECT * FROM connector_jobs WHERE status='queued' ORDER BY started_at LIMIT 8"
        )
        for row in candidates:
            won = await self._meta.execute_rowcount(
                "UPDATE connector_jobs SET status='running', heartbeat=? WHERE id=? AND status='queued'",
                (_now(), row["id"]),
            )
            if won == 1:
                return row
        return None

    async def list_stale_preparing_jobs(self, cutoff: str) -> list[dict]:
        return await self._meta.fetchall(
            "SELECT id FROM connector_jobs WHERE status='preparing' "
            "AND heartbeat IS NOT NULL AND heartbeat < ?",
            (cutoff,),
        )

    async def list_stale_running_jobs(self, cutoff: str) -> list[dict]:
        return await self._meta.fetchall(
            "SELECT id, connector_id FROM connector_jobs WHERE status='running' "
            "AND heartbeat IS NOT NULL AND heartbeat < ?",
            (cutoff,),
        )

    async def find_inflight_sibling(self, cid: str, job_id: str) -> dict | None:
        return await self._meta.fetchone(
            "SELECT id FROM connector_jobs WHERE connector_id=? "
            "AND status IN ('queued', 'preparing') AND id<>? LIMIT 1",
            (cid, job_id),
        )

    async def get_running_job_heartbeat(self, cid: str) -> dict | None:
        return await self._meta.fetchone(
            "SELECT id, heartbeat FROM connector_jobs WHERE connector_id=? AND status='running'",
            (cid,),
        )

    async def list_running_jobs(self) -> list[dict]:
        return await self._meta.fetchall(
            "SELECT id, connector_id FROM connector_jobs WHERE status='running'"
        )

    async def summarize_jobs_by_status(self, cid: str) -> list[dict]:
        return await self._meta.fetchall(
            "SELECT status, count(*) AS n FROM connector_jobs WHERE connector_id=? GROUP BY status",
            (cid,),
        )

    # ------------------------------------------------------------------
    # objects — writes
    # ------------------------------------------------------------------
    async def write_object_row(
        self, cid: str, relpath: str, st, indexable: bool, search_status: str, chunk_count: int
    ) -> None:
        """UPSERT the ``objects`` registry row. Shared by the inline _index_object tail,
        the rename branch, and the pipeline success hook."""
        import os

        await self._meta.execute(
            "INSERT INTO objects (connector_id, object_uri, parent_path, type, media_type, size_hint, "
            " fingerprint, indexable, last_seen, search_status, chunk_count, indexed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(connector_id, object_uri) DO UPDATE SET "
            " type=excluded.type, media_type=excluded.media_type, size_hint=excluded.size_hint, "
            " fingerprint=excluded.fingerprint, indexable=excluded.indexable, last_seen=excluded.last_seen, "
            " search_status=excluded.search_status, chunk_count=excluded.chunk_count, indexed_at=excluded.indexed_at",
            (
                cid,
                relpath,
                os.path.dirname(relpath) or "/",
                st.type,
                st.media_type,
                st.size_hint,
                st.fingerprint,
                1 if indexable else 0,
                _now(),
                search_status,
                chunk_count,
                _now(),
            ),
        )

    async def delete_object_row(self, cid: str, object_uri: str) -> None:
        await self._meta.execute(
            "DELETE FROM objects WHERE connector_id=? AND object_uri=?", (cid, object_uri)
        )

    async def delete_object_task_job_rows_for_connector(self, cid: str) -> None:
        """Delete the three target tables' rows for a connector (object_tasks /
        connector_jobs / objects). connector_state / file_state / connectors are
        handled by the caller (not this repo's three-table scope)."""
        for tbl, col in (
            ("object_tasks", "connector_id"),
            ("connector_jobs", "connector_id"),
            ("objects", "connector_id"),
        ):
            await self._meta.execute(f"DELETE FROM {tbl} WHERE {col}=?", (cid,))

    # ------------------------------------------------------------------
    # objects — reads
    # ------------------------------------------------------------------
    async def get_object_fingerprint(self, cid: str, object_uri: str) -> str | None:
        row = await self._meta.fetchone(
            "SELECT fingerprint FROM objects WHERE connector_id=? AND object_uri=?",
            (cid, object_uri),
        )
        return row["fingerprint"] if row else None

    async def get_object_search_status(self, cid: str, object_uri: str) -> dict | None:
        return await self._meta.fetchone(
            "SELECT search_status, indexable FROM objects WHERE connector_id=? AND object_uri=?",
            (cid, object_uri),
        )

    async def list_object_uris_for_connector(self, cid: str) -> list[dict]:
        return await self._meta.fetchall(
            "SELECT object_uri FROM objects WHERE connector_id=?", (cid,)
        )

    async def list_objects_with_chunks(self, cid: str) -> list[dict]:
        return await self._meta.fetchall(
            "SELECT object_uri, chunk_count FROM objects WHERE connector_id=? AND chunk_count>0",
            (cid,),
        )

    async def count_indexed_objects(self, cid: str) -> int:
        row = await self._meta.fetchone(
            "SELECT count(*) AS n FROM objects WHERE connector_id=? AND search_status='indexed'",
            (cid,),
        )
        return (row or {}).get("n", 0) or 0

    async def summarize_objects_by_search_status(self, cid: str) -> list[dict]:
        return await self._meta.fetchall(
            "SELECT search_status, count(*) AS n FROM objects WHERE connector_id=? GROUP BY search_status",
            (cid,),
        )

    async def summarize_objects_totals(self, cid: str) -> dict:
        return await self._meta.fetchone(
            "SELECT count(*) AS n, sum(chunk_count) AS chunks FROM objects WHERE connector_id=?",
            (cid,),
        )

    async def list_not_indexed_in_scope(self, cid: str, rel: str) -> list[dict]:
        """Grep linear-scan candidates: not_indexed objects under ``rel`` (path-component
        boundary, LIKE wildcards escaped). ``rel == '/'`` means the whole connector."""
        if rel == "/":
            return await self._meta.fetchall(
                "SELECT object_uri FROM objects WHERE connector_id=? AND search_status='not_indexed'",
                (cid,),
            )
        base = rel.rstrip("/")
        esc = base.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return await self._meta.fetchall(
            "SELECT object_uri FROM objects WHERE connector_id=? AND search_status='not_indexed' "
            "AND (object_uri = ? OR object_uri LIKE ? ESCAPE '\\')",
            (cid, base, esc + "/%"),
        )

    # ------------------------------------------------------------------
    # connectors — writes
    # ------------------------------------------------------------------
    async def insert_connector(
        self, cid: str, connector_uri: str, ctype: str, config_json: str
    ) -> None:
        await self._meta.execute(
            "INSERT INTO connectors (id, namespace_id, root_uri, type, status, config_json, registered_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (cid, self._ns, connector_uri, ctype, "active", config_json, _now()),
        )

    async def update_connector_config(self, cid: str, config_json: str) -> None:
        await self._meta.execute(
            "UPDATE connectors SET config_json=? WHERE id=?", (config_json, cid)
        )

    async def set_connector_removing(self, cid: str) -> None:
        await self._meta.execute("UPDATE connectors SET status='removing' WHERE id=?", (cid,))

    async def delete_connector(self, cid: str) -> None:
        await self._meta.execute("DELETE FROM connectors WHERE id=?", (cid,))

    # ------------------------------------------------------------------
    # connectors — reads
    # ------------------------------------------------------------------
    async def get_connector_id_by_uri(self, connector_uri: str) -> str | None:
        row = await self._meta.fetchone(
            "SELECT id FROM connectors WHERE namespace_id=? AND root_uri=?",
            (self._ns, connector_uri),
        )
        return row["id"] if row else None

    async def get_connector_id_and_config_by_uri(self, connector_uri: str) -> dict | None:
        return await self._meta.fetchone(
            "SELECT id, config_json FROM connectors WHERE namespace_id=? AND root_uri=?",
            (self._ns, connector_uri),
        )

    async def get_connector_config(self, cid: str) -> dict | None:
        return await self._meta.fetchone("SELECT config_json FROM connectors WHERE id=?", (cid,))

    async def get_connector_config_and_status(self, cid: str) -> dict | None:
        return await self._meta.fetchone(
            "SELECT config_json, status FROM connectors WHERE id=?", (cid,)
        )

    async def get_connector_status(self, cid: str) -> str | None:
        row = await self._meta.fetchone("SELECT status FROM connectors WHERE id=?", (cid,))
        return row["status"] if row else None

    async def get_connector_root_type_config(self, cid: str) -> dict | None:
        return await self._meta.fetchone(
            "SELECT root_uri, type, config_json FROM connectors WHERE id=?", (cid,)
        )

    async def get_connector_row(self, cid: str) -> dict | None:
        return await self._meta.fetchone(
            "SELECT id, root_uri, type, status, registered_at FROM connectors WHERE id=?",
            (cid,),
        )

    async def get_connector_row_by_uri(self, connector_uri: str) -> dict | None:
        return await self._meta.fetchone(
            "SELECT id, root_uri, type, status, registered_at FROM connectors "
            "WHERE namespace_id=? AND root_uri=?",
            (self._ns, connector_uri),
        )

    async def list_connectors_summary(self) -> list[dict]:
        return await self._meta.fetchall(
            "SELECT id, root_uri FROM connectors WHERE namespace_id=?", (self._ns,)
        )

    async def list_connectors_all(self) -> list[dict]:
        return await self._meta.fetchall(
            "SELECT * FROM connectors WHERE namespace_id=?", (self._ns,)
        )

    async def has_any_connector(self) -> bool:
        row = await self._meta.fetchone(
            "SELECT id FROM connectors WHERE namespace_id=? LIMIT 1", (self._ns,)
        )
        return row is not None

    async def has_connector_uri(self, connector_uri: str) -> bool:
        row = await self._meta.fetchone(
            "SELECT id FROM connectors WHERE namespace_id=? AND root_uri=? LIMIT 1",
            (self._ns, connector_uri),
        )
        return row is not None
