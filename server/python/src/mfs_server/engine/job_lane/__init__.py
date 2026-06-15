"""Job Lane (§3.3 / §3.5 / §6.4): directory summaries as their own scheduling lane.

The Object Lane works at object granularity (per-object, self-contained, in the object_tasks
table). The Job Lane works at job granularity: a directory summary folds a directory's
children, with a DAG dependency (sub-dirs before parents) and bottom-up ordering. So it lives
OUTSIDE object_tasks: a per-job in-memory DirTree (job_lane/tree.py) + a global priority queue
(job_lane/queue.py) + a SummaryWorker pool (job_lane/worker.py). The two lanes run in parallel
and both emit Chunks into the SAME chunks_q, so the EmbedConsumer indexes them uniformly.

Coordinator hooks the engine calls:
  register_job(job_id, connector_uri, plugin)  — at sync start
  on_yield_object_change(job_id, uri, okind)   — per non-deleted sync() yield
  on_sync_done(job_id)                          — at sync end (finalize the tree)
  on_embed_succeeded(task_uri, job_id)          — registered with EmbedConsumer; counts a
                                                  dir_summary as persisted (file successes are
                                                  ignored — files do not gate a dir)
  await_done(job_id)                     — block until all of a job's dir summaries
                                                  are computed + persisted
  evict_job(job_id)                             — free a terminal job's DirTree

Gated on cfg.summary.enabled (the master switch): with summaries off the coordinator
is inert and every hook is a no-op, so the default path is unchanged.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from ..pipeline import TaskEnvelope
from ..producers.base import (
    Chunk,
    DescriptionConcurrencyGate,
    EndOfTask,
    SummaryConcurrencyGate,
)
from .queue import SummaryQueue
from .tree import DirTreeBuilder
from .worker import run_summary_worker

__all__ = ["JobLaneCoordinator", "build_job_lane"]


class JobLaneCoordinator:
    def __init__(
        self,
        cfg,
        *,
        tx_cache,
        summary,
        vlm,
        converter,
        chunks_q,
        artifacts=None,
        namespace_id="default",
        description_gate=None,
        summary_gate=None,
    ):
        self.cfg = cfg
        self.enabled = bool(cfg.summary.enabled)  # master switch (§7.2 [summary].enabled)
        # whether VLM descriptions exist at all; a folded-in image must not trigger a VLM call
        # when [description] is off (no provider/budget for it).
        self.description_enabled = bool(cfg.description.enabled)
        self.do_dir = bool(cfg.summary.dir)  # run recursive directory summaries
        self.do_file = bool(cfg.summary.file)  # run per-file summaries (§6.4.7, default off)
        self.recursive = True  # directory summaries are always recursive bottom-up now
        self.tx_cache = tx_cache
        self.summary = summary
        self.vlm = vlm
        self.converter = converter
        self.chunks_q = chunks_q
        # Artifact store + namespace let a SummaryWorker reuse the Object Lane's converted_md
        # for a child document (via the converter currency token) instead of re-converting.
        self.artifacts = artifacts
        self.namespace_id = namespace_id
        # Concurrency gates (§5.5) shared with the Object Lane producers, so a SummaryWorker's summary /
        # VLM provider call draws from the same in-flight budget as image / table_schema. Default
        # to fresh gates sized by cfg when none is injected (e.g. unit tests).
        self.summary_gate = summary_gate or SummaryConcurrencyGate(cfg.summary.concurrency)
        self.description_gate = description_gate or DescriptionConcurrencyGate(
            cfg.description.concurrency
        )

        self.queue = SummaryQueue()
        self.builders: dict[str, DirTreeBuilder] = {}
        self.job_plugins: dict[str, Any] = {}
        # per-job completion: {"total": int, "persisted": int, "event": asyncio.Event}
        self._completion: dict[str, dict] = {}
        self._file_summary_candidates: dict[str, list] = {}  # file_summary opt-in (§6.4.7)
        self._tasks: list[asyncio.Task] = []

    # --- lifecycle ---
    def start(self) -> None:
        if not self.enabled or self._tasks:
            return
        self._tasks.append(asyncio.create_task(self.queue.dispatcher()))
        for i in range(self._worker_count()):
            self._tasks.append(asyncio.create_task(run_summary_worker(self, i)))

    def _worker_count(self) -> int:
        # [summary].concurrency caps the SummaryWorker pool size.
        return max(1, int(self.cfg.summary.concurrency))

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._tasks = []

    # --- sync-time hooks ---
    def register_job(self, job_id: str, connector_uri: str, plugin: Any) -> None:
        if not self.enabled:
            return
        self.builders[job_id] = DirTreeBuilder(job_id, connector_uri, recursive=self.recursive)
        self.job_plugins[job_id] = plugin
        self._completion[job_id] = {"total": 0, "persisted": 0, "event": asyncio.Event()}

    def on_yield_object_change(self, job_id: str, uri: str, okind: str) -> None:
        if not self.enabled:
            return
        builder = self.builders.get(job_id)
        if builder is None:
            return
        if self.do_dir:
            builder.add(uri, okind)
        # file_summary (§6.4.7): [summary].file opt-in, default off. When on, each file also
        # gets its own summary task. Plumbing only — collected here; processed by the same
        # worker pool. Default-off so it stays a no-op and is not exercised by tests.
        if self.do_file:
            self._file_summary_candidates.setdefault(job_id, []).append((uri, okind))

    def on_sync_done(self, job_id: str) -> None:
        if not self.enabled:
            return
        builder = self.builders.get(job_id)
        if builder is None:
            return
        builder.finalize(self.queue)  # flips sync_done; pushes every leaf dir (no sub-dirs)
        st = self._completion.get(job_id)
        if st is not None:
            st["total"] = len(builder.tree)
            if st["total"] == 0:  # no dirs (empty sync / no hierarchy) -> trivially done
                st["event"].set()

    # --- EmbedConsumer success hook (dir_summary persist accounting) ---
    def on_embed_succeeded(
        self,
        task_uri: str,
        job_id: Optional[str],
        chunk_count: int = 0,
        partial: bool = False,
        error: Optional[str] = None,
    ) -> None:
        # chunk_count / partial / error are unused here, but accepted so the single
        # finalize-hook signature carries them for the objects-table updater. Files no longer
        # gate any dir (a dir folds source content, not embeddings), so a file's success is
        # ignored here; only a persisted directory_summary advances the completion count.
        if not self.enabled or job_id is None:
            return
        builder = self.builders.get(job_id)
        if builder is None:
            return
        cu = builder.connector_uri
        relpath = task_uri[len(cu) :] if task_uri.startswith(cu) else task_uri
        if relpath in builder.tree:
            # a directory_summary chunk for this job was just persisted
            st = self._completion.get(job_id)
            if st is not None:
                st["persisted"] += 1
                if st["persisted"] >= st["total"]:
                    st["event"].set()

    # --- worker callback: emit a dir summary into chunks_q ---
    async def emit_dir_summary(
        self, job_id: str, connector_uri: str, dir_uri: str, summ: str
    ) -> None:
        """Emit one directory as a per-object task into chunks_q: a directory_summary Chunk
        (when non-empty) plus an EndOfTask. The EmbedConsumer delete-then-upserts it (per-object
        atomic) exactly like an Object Lane chunk; an empty summary becomes a chunk-less task that just
        purges any stale summary. Either way the persist success hook lands in on_embed_succeeded."""
        full_uri = connector_uri + dir_uri
        task_id = f"reduce:{job_id}:{dir_uri}"
        if summ and summ.strip():
            chunk = Chunk(
                content=summ,
                chunk_kind="directory_summary",
                locator=None,
                uri=full_uri,
                connector_job_id=job_id,
            )
            await self.chunks_q.put(
                TaskEnvelope(
                    task_id=task_id,
                    task_uri=full_uri,
                    connector_uri=connector_uri,
                    job_id=job_id,
                    payload=chunk,
                )
            )
        await self.chunks_q.put(
            TaskEnvelope(
                task_id=task_id,
                task_uri=full_uri,
                connector_uri=connector_uri,
                job_id=job_id,
                payload=EndOfTask(),
            )
        )

    # --- completion + teardown ---
    async def await_done(self, job_id: str) -> None:
        if not self.enabled:
            return
        st = self._completion.get(job_id)
        if st is None:
            return
        await st["event"].wait()

    def is_done(self, job_id: str) -> bool:
        """Synchronous completion check for the ConnectorJobWatcher: True when this job has
        no outstanding directory summaries (or no reduce work at all)."""
        if not self.enabled:
            return True
        st = self._completion.get(job_id)
        if st is None:
            return True  # job not tracked by the Job Lane -> nothing to wait on
        return st["event"].is_set()

    def active_jobs(self) -> list[str]:
        """Jobs whose in-memory DirTree is still held (bounded set the watcher scans to
        evict reduce state of terminal jobs)."""
        return list(self.builders.keys())

    def evict_job(self, job_id: str) -> None:
        self.builders.pop(job_id, None)
        self.job_plugins.pop(job_id, None)
        self._completion.pop(job_id, None)
        self._file_summary_candidates.pop(job_id, None)
        self.queue.evict_job(job_id)

    # --- crash recovery (§6.4.5) ---
    def recover_job(
        self,
        job_id: str,
        connector_uri: str,
        plugin: Any,
        objects: list[tuple[str, str, str]],  # (object_uri, okind, status)
        existing_summaries: list[tuple[str, str]],  # (dir_uri relative, content)
    ) -> None:
        """Rebuild a 'running' job's DirTree after a restart (server died mid-Phase-2). The
        sync had already finished, so sync_done is assumed True. Files do not gate a dir, so
        only sub-dir dependencies matter: dirs whose summary already reached Milvus are seeded
        (their parent pre-decremented + counted as persisted) so they are not recomputed."""
        if not self.enabled:
            return
        builder = DirTreeBuilder(job_id, connector_uri, recursive=self.recursive)
        for uri, okind, _ in objects:
            builder.add(uri, okind)
        self.builders[job_id] = builder
        self.job_plugins[job_id] = plugin
        builder.sync_done = True
        persisted = 0
        # dirs already summarized: seed + don't recompute
        existing = dict(existing_summaries)
        for dir_uri, content in existing_summaries:
            node = builder.tree.get(dir_uri)
            if node is not None:
                node.summary = content
                persisted += 1
                if node.parent and node.parent in builder.tree:
                    builder.tree[node.parent].pending -= 1
        self._completion[job_id] = {
            "total": len(builder.tree),
            "persisted": persisted,
            "event": asyncio.Event(),
        }
        # push any dir that is now ready and not already summarized
        for dir_uri, node in builder.tree.items():
            if dir_uri not in existing and node.pending == 0:
                self.queue.push(job_id, dir_uri, node.depth)
        st = self._completion[job_id]
        if st["persisted"] >= st["total"]:
            st["event"].set()


def build_job_lane(
    cfg,
    *,
    tx_cache,
    summary,
    vlm,
    converter,
    chunks_q,
    artifacts=None,
    namespace_id="default",
    description_gate=None,
    summary_gate=None,
) -> JobLaneCoordinator:
    """Construct the Job Lane coordinator. The caller (engine) registers its on_embed_succeeded
    with the EmbedConsumer and calls start() after the event loop is running. The
    description/summary gates are shared with the Object Lane producers (§5.5); the artifact store is
    shared too, so the Job Lane reuses the Object Lane's converted_md."""
    return JobLaneCoordinator(
        cfg,
        tx_cache=tx_cache,
        summary=summary,
        vlm=vlm,
        converter=converter,
        chunks_q=chunks_q,
        artifacts=artifacts,
        namespace_id=namespace_id,
        description_gate=description_gate,
        summary_gate=summary_gate,
    )
