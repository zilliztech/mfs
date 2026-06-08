"""ConnectorJobWatcher (§5.7): an independent lightweight coroutine that polls connector_jobs
and finalizes them out-of-band.

In the new pipeline there is no per-job worker loop bound to a job, so something has to notice
when a job's work is finished and flip connector_jobs.status. The watcher does that by SQL on a
short interval, and folds in the Reduce subsystem teardown:

  - completion: a 'running' job with >=1 task and no pending/running tasks AND no outstanding
    directory summaries -> 'succeeded' + evict the job's DirTree.
  - cancel: a job the user cancelled (status='cancelled' — set immediately by Engine.cancel_job)
    that still holds a DirTree or pending tasks -> cancel the pending tasks + evict.

The watcher does NOT trip the circuit breaker. That is owned solely by _run_job_loop, which
tracks CONSECUTIVE failures and aborts the job (cancelling pending tasks + returning a reason
that _finalize_job records as 'failed'). The watcher is reconciliation only; duplicating the
breaker here against a CUMULATIVE failed count would disagree with the loop's semantics and
fail jobs the loop deliberately kept running (interspersed failures below the consecutive cap).

It is idempotent: terminal transitions use `WHERE status='running'` conditional UPDATEs and
evict removes the DirTree, so a second tick does not re-finalize or re-evict.

Cancellation is detected via the existing `status='cancelled'` rather than a new
`cancel_requested` column: the metadata schema uses a fail-fast version guard (no auto-migration),
so adding a column/table would risk existing-DB compatibility. cancel_job already sets the status
synchronously; the watcher consolidates the missing reduce-evict + pending-task cleanup.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

# Poll cadence. Kept a source constant (not TOML): 1s is responsive without meaningful DB load
# (a couple of GROUP BY queries). The [object_task] business section can fold this in later.
_JOB_WATCH_INTERVAL_S = 1.0

# Task statuses that count as "not done yet" — a job with any of these is still in flight.
_LIVE_TASK_STATUSES = ("pending", "running")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConnectorJobWatcher:
    def __init__(
        self,
        meta: Any,
        reduce_coordinator: Any,
        *,
        poll_interval_s: float = _JOB_WATCH_INTERVAL_S,
    ):
        self.meta = meta
        self.reduce = reduce_coordinator
        self.poll_interval_s = poll_interval_s
        self._stop = asyncio.Event()

    async def run(self) -> None:
        """Poll until stopped. One bad tick is logged, never fatal — the watcher must outlive
        any transient DB hiccup or it would silently stop finalizing every job."""
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception as e:  # noqa: BLE001
                print(f"mfs-server: WARNING job watcher tick failed: {e}", flush=True)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval_s)
            except asyncio.TimeoutError:
                pass

    def stop(self) -> None:
        self._stop.set()

    async def tick(self) -> None:
        """One poll cycle: finalize completed/failed running jobs, then clean up terminal
        jobs that still hold Reduce state."""
        await self._sweep_running_jobs()
        await self._evict_terminal_reduce_jobs()

    # --- running jobs -> completion / failure ---
    async def _sweep_running_jobs(self) -> None:
        running = await self.meta.fetchall("SELECT id FROM connector_jobs WHERE status='running'")
        for row in running:
            job_id = row["id"]
            counts = await self.meta.fetchall(
                "SELECT status, count(*) AS n FROM object_tasks WHERE connector_job_id=? GROUP BY status",
                (job_id,),
            )
            cmap = {r["status"]: r["n"] for r in counts}
            total = sum(cmap.values())
            live = sum(cmap.get(s, 0) for s in _LIVE_TASK_STATUSES)

            # Completion needs >=1 task so a job mid-enumeration (process=True jobs are
            # created 'running' and accrue tasks as sync() yields) isn't finalized at 0 tasks;
            # while enumerating, the just-inserted tasks are 'pending', so `live > 0` holds.
            if total > 0 and live == 0 and self.reduce.is_reduce_done(job_id):
                won = await self.meta.execute_rowcount(
                    "UPDATE connector_jobs SET status='succeeded', finished_at=? "
                    "WHERE id=? AND status='running'",
                    (_now(), job_id),
                )
                if won == 1:
                    self.reduce.evict_job(job_id)

    # --- terminal jobs still holding a DirTree (cancelled / late evict) ---
    async def _evict_terminal_reduce_jobs(self) -> None:
        for job_id in list(self.reduce.active_jobs()):
            row = await self.meta.fetchone(
                "SELECT status FROM connector_jobs WHERE id=?", (job_id,)
            )
            status = row["status"] if row else None
            if status in ("cancelled", "failed", "succeeded"):
                if status == "cancelled":
                    await self.meta.execute(
                        "UPDATE object_tasks SET status='cancelled' "
                        "WHERE connector_job_id=? AND status='pending'",
                        (job_id,),
                    )
                self.reduce.evict_job(job_id)
