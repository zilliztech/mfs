"""WorkerScheduler: queue claim + concurrent workers + reclaim.

Single-direction dep on IngestOrchestrator (run_job + finalize_job), no cycle.

The two-layer try/except is the load-bearing invariant: the OUTER
`except Exception: jid = None` in run_forever._loop keeps a single failed
job from killing the worker coroutine (with the sqlite single worker that
would wedge all ingest); the INNER except in run_worker_once is business-
level (mark the job failed, keep the queue draining).
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from .state import ConnectorStateStore

logger = logging.getLogger(__name__)

_JOB_STALE_AFTER_S = 120  # no heartbeat for this long => worker presumed dead
_WORKER_CONNECT_TIMEOUT_S = 30  # bound plugin.connect() in the worker so a hanging/unreachable
# connector fails its job cleanly instead of blocking the single in-process worker forever


class WorkerScheduler:
    """Queue layer: claim job -> connect(timeout) -> ingest.run_job +
    ingest.finalize_job -> state.commit; concurrent workers + reclaim.
    Single-direction dep on ingest, no cycle."""

    def __init__(self, cfg, infra, factory, objects, ingest):
        self._cfg = cfg
        self._infra = infra
        self._factory = factory
        self._objects = objects
        self._ingest = ingest

    async def run_worker_once(self) -> str | None:
        """Claim + process one queued job. Returns its id, or None if queue empty."""
        import json

        job = await self._objects.claim_queued_job()
        if not job:
            return None
        cid = job["connector_id"]
        crow = await self._objects.get_connector_root_type_config(cid)
        connector_uri, ctype = crow["root_uri"], crow["type"]
        stored_cfg = json.loads(crow["config_json"]) if crow["config_json"] else {}
        plugin = None
        try:
            plugin = self._factory.build_plugin(ctype, stored_cfg, cid).plugin
            # Bound connect(): an unreachable/hanging connector (or one whose persisted creds
            # no longer resolve) must fail THIS job cleanly, not block the single in-process
            # sqlite worker forever - one bad connector cannot be allowed to wedge all ingest.
            await asyncio.wait_for(plugin.connect(), timeout=_WORKER_CONNECT_TIMEOUT_S)
            aborted = await self._ingest.run_job(job["id"], cid, connector_uri, plugin)
            await self._ingest.finalize_job(job["id"], aborted)
            # commit the deferred connector state only on a FULLY clean run: a
            # failed/cancelled/partial job leaves the cursor where it was, so a
            # partial job's failed objects (and the successful ones alongside
            # them) get reconsidered on the next sync rather than the cursor
            # skipping past them. Each connector's own fingerprint check keeps
            # that cheap -- the already-succeeded objects get skipped quickly,
            # only the failed ones actually redo real work.
            if aborted is None:
                jrow = await self._objects.get_job_state_and_status(job["id"])
                if jrow and jrow["status"] == "succeeded" and jrow["state_snapshot"]:
                    await ConnectorStateStore(self._infra.meta, cid).apply(
                        json.loads(jrow["state_snapshot"])
                    )
        except Exception as e:  # noqa: BLE001
            # Move the claimed job to a terminal 'failed' state and release the worker, so the
            # queue keeps draining. Without this a connect timeout/exception would leave the
            # job stuck 'running' and (with the single sqlite worker) wedge every later job.
            reason = (
                "connector_unhealthy: connect timed out"
                if isinstance(e, asyncio.TimeoutError)
                else f"sync_error: {e}"
            )
            await self._objects.fail_running_tasks_for_job(job["id"], str(reason))
            await self._objects.fail_inflight_job(job["id"], str(reason))
            logger.warning("sync job %s for %s failed: %s", job["id"], connector_uri, reason)
        finally:
            if plugin is not None:
                try:
                    await plugin.close()
                except Exception:  # noqa: BLE001
                    pass
        return job["id"]

    def _resolve_concurrency(self, concurrency=None) -> int:
        c = concurrency if concurrency is not None else self._cfg.chunks_producer.concurrency
        if c == "auto":
            return max(1, (os.cpu_count() or 2))
        try:
            return max(1, int(c))
        except (TypeError, ValueError):
            return 1

    async def _reclaim_stale_jobs(self, stale_after_s: int = _JOB_STALE_AFTER_S) -> None:
        """Housekeeping: a job whose worker died keeps status='running' (or 'preparing')
        with a stale heartbeat forever. Recover such jobs so a live worker resumes them.

        Each job is recovered independently and any error is LOGGED, never silently swallowed
        - a single un-recoverable job must not abort (and thus starve) the reclaim of every
        other orphan, which would wedge crash-recovery for all connectors."""
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=stale_after_s)).isoformat()

        # Fail stale 'preparing' jobs: one whose process died mid-enumeration never started
        # running, and while it lingers it holds the connector's one-active-job slot, blocking
        # any new sync from being enqueued for that connector at all.
        try:
            stale_prep = await self._objects.list_stale_preparing_jobs(cutoff)
        except Exception as e:  # noqa: BLE001
            logger.warning("reclaim: listing stale preparing jobs failed: %s", e)
            stale_prep = []
        for j in stale_prep:
            try:
                await self._objects.fail_stale_preparing_job(j["id"])
            except Exception as e:  # noqa: BLE001
                logger.warning("reclaim: failing stale preparing job %s: %s", j["id"], e)

        try:
            stale = await self._objects.list_stale_running_jobs(cutoff)
        except Exception as e:  # noqa: BLE001
            logger.warning("reclaim: listing stale running jobs failed: %s", e)
            return
        for j in stale:
            try:
                # ux_jobs_one_active guarantees no other non-terminal job exists for this
                # connector right now, so the requeue below can never collide with a sibling.
                # reset the dead worker's in-flight tasks back to pending FIRST, else the
                # re-claiming worker sees only 'pending', finds none, and finalizes the job
                # 'succeeded' while a task is still stuck 'running' (P1 crash-recovery gap).
                await self._objects.reset_running_tasks_to_pending(j["id"])
                await self._objects.requeue_stale_running_job(j["id"])
            except Exception as e:  # noqa: BLE001 - one un-recoverable orphan must not starve the rest
                logger.warning("reclaim: recovering stale running job %s: %s", j["id"], e)

    async def run_forever(self, poll_interval: float = 1.0, concurrency=None) -> None:
        """Drain the queued-job queue with `concurrency` parallel workers. Each worker
        atomically claims a distinct job (the conditional claim is race-free), so N
        connectors' sync jobs run in parallel. Idle workers run a housekeeping pass that
        reclaims jobs orphaned by a crashed worker (stale heartbeat)."""
        n = self._resolve_concurrency(concurrency)

        async def _loop() -> None:
            while True:
                try:
                    jid = await self.run_worker_once()
                except Exception:  # noqa: BLE001 - a single job must NEVER kill the worker
                    # coroutine; with the sqlite single worker that would wedge all ingest.
                    jid = None
                if jid is None:
                    await self._reclaim_stale_jobs()
                    await asyncio.sleep(poll_interval)

        await asyncio.gather(*[_loop() for _ in range(n)])
