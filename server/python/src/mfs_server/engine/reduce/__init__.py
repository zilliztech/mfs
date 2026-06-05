"""Reduce subsystem (§3.3 / §3.5 / §6.4): dir_summary as its own scheduling lane.

Unlike Map work (per-object, self-contained, in the object_tasks table), a directory summary
is a REDUCE over a directory's children — it has a DAG dependency (sub-dirs before parents)
and bottom-up ordering. So it lives OUTSIDE object_tasks: a per-job in-memory DirTree
(reduce/tree.py) + a global priority queue (reduce/queue.py) + a SummaryWorker pool
(reduce/worker.py). Summaries are emitted as Chunks into the SAME chunks_q the Map producers
use, so the EmbedConsumer indexes them uniformly.

Coordinator hooks the engine calls:
  register_job(job_id, connector_uri, plugin)  — at sync start
  on_yield_object_change(job_id, uri, okind)   — per non-deleted sync() yield
  on_sync_done(job_id)                          — at sync end (finalize the tree)
  on_embed_succeeded(task_uri, job_id)          — registered with EmbedConsumer; drives both
                                                  the Map→Reduce file notification AND the
                                                  dir_summary persist accounting
  await_reduce_done(job_id)                     — block until all of a job's dir summaries
                                                  are computed + persisted
  evict_job(job_id)                             — free a terminal job's DirTree

Gated on cfg.summary.enabled (the legacy master switch): with summaries off the coordinator
is inert and every hook is a no-op, so the default path is unchanged.
"""

from __future__ import annotations

import asyncio
import posixpath
from typing import Any, Optional

from ..pipeline import TaskEnvelope
from ..producers.base import Chunk, EndOfTask
from .queue import SummaryQueue
from .tree import DirTreeBuilder
from .worker import run_summary_worker

__all__ = ["ReduceCoordinator", "build_reduce_subsystem"]


class ReduceCoordinator:
    def __init__(self, cfg, *, tx_cache, summary, vlm, converter, chunks_q):
        self.cfg = cfg
        self.enabled = bool(cfg.summary.enabled)
        self.recursive = bool(getattr(cfg.summary, "dir_recursive", True))
        self.tx_cache = tx_cache
        self.summary = summary
        self.vlm = vlm
        self.converter = converter
        self.chunks_q = chunks_q

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
        # [summary].concurrency lands with the TOML rename (step 11); until then reuse the
        # existing [summary].batch_size knob (default 20) as the SummaryWorker pool size.
        n = getattr(self.cfg.summary, "concurrency", None) or getattr(self.cfg.summary, "batch_size", 20)
        return max(1, int(n))

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
        builder.add(uri, okind)
        # file_summary (§6.4.7): [summary].file opt-in, default off. When on, each file also
        # gets its own summary task. Plumbing only — collected here; processed by the same
        # worker pool. Default-off so it stays a no-op and is not exercised by tests.
        if getattr(self.cfg.summary, "file", False):
            self._file_summary_candidates.setdefault(job_id, []).append((uri, okind))

    def on_sync_done(self, job_id: str) -> None:
        if not self.enabled:
            return
        builder = self.builders.get(job_id)
        if builder is None:
            return
        builder.finalize(self.queue)
        st = self._completion.get(job_id)
        if st is not None:
            st["total"] = len(builder.tree)
            if st["total"] == 0:  # no dirs (empty sync / no hierarchy) -> trivially done
                st["event"].set()

    # --- EmbedConsumer success hook (Map→Reduce notify §6.4.4 + dir persist accounting) ---
    def on_embed_succeeded(self, task_uri: str, job_id: Optional[str]) -> None:
        if not self.enabled or job_id is None:
            return
        builder = self.builders.get(job_id)
        if builder is None:
            return
        cu = builder.connector_uri
        relpath = task_uri[len(cu):] if task_uri.startswith(cu) else task_uri
        if relpath in builder.tree:
            # a directory_summary chunk for this job was just persisted
            st = self._completion.get(job_id)
            if st is not None:
                st["persisted"] += 1
                if st["persisted"] >= st["total"]:
                    st["event"].set()
            return
        # otherwise a Map file task succeeded -> notify its parent dir
        if not builder.sync_done:
            return  # more children may still be added before finalize
        parent = posixpath.dirname(relpath) or "/"
        node = builder.tree.get(parent)
        if node is None:
            return
        node.pending -= 1
        if node.pending == 0:
            self.queue.push(job_id, parent, node.depth)

    # --- worker callback: emit a dir summary into chunks_q ---
    async def emit_dir_summary(self, job_id: str, connector_uri: str, dir_uri: str, summ: str) -> None:
        """Emit one directory as a per-object task into chunks_q: a directory_summary Chunk
        (when non-empty) plus an EndOfTask. The EmbedConsumer delete-then-upserts it (per-object
        atomic) exactly like a Map chunk; an empty summary becomes a chunk-less task that just
        purges any stale summary. Either way the persist success hook lands in on_embed_succeeded."""
        full_uri = connector_uri + dir_uri
        task_id = f"reduce:{job_id}:{dir_uri}"
        if summ and summ.strip():
            chunk = Chunk(
                content=summ, chunk_kind="directory_summary", locator=None,
                uri=full_uri, connector_job_id=job_id,
            )
            await self.chunks_q.put(
                TaskEnvelope(task_id=task_id, task_uri=full_uri, connector_uri=connector_uri,
                             job_id=job_id, payload=chunk)
            )
        await self.chunks_q.put(
            TaskEnvelope(task_id=task_id, task_uri=full_uri, connector_uri=connector_uri,
                         job_id=job_id, payload=EndOfTask())
        )

    # --- completion + teardown ---
    async def await_reduce_done(self, job_id: str) -> None:
        if not self.enabled:
            return
        st = self._completion.get(job_id)
        if st is None:
            return
        await st["event"].wait()

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
        sync had already finished, so sync_done is assumed True. Already-succeeded files
        pre-decrement their parent's pending; dirs whose summary already reached Milvus are
        seeded (and their parent pre-decremented + counted as persisted) so they are not
        recomputed."""
        if not self.enabled:
            return
        builder = DirTreeBuilder(job_id, connector_uri, recursive=self.recursive)
        for uri, okind, _ in objects:
            builder.add(uri, okind)
        self.builders[job_id] = builder
        self.job_plugins[job_id] = plugin
        builder.sync_done = True
        persisted = 0
        # files already indexed: their parent no longer waits on them
        for uri, _, status in objects:
            if status == "succeeded":
                node = builder.tree.get(posixpath.dirname(uri) or "/")
                if node is not None:
                    node.pending -= 1
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


def build_reduce_subsystem(cfg, *, tx_cache, summary, vlm, converter, chunks_q) -> ReduceCoordinator:
    """Construct the Reduce coordinator. The caller (engine) registers its on_embed_succeeded
    with the EmbedConsumer and calls start() after the event loop is running."""
    return ReduceCoordinator(
        cfg, tx_cache=tx_cache, summary=summary, vlm=vlm, converter=converter, chunks_q=chunks_q
    )
