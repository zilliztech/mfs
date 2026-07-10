"""PipelineSupervisor - per-Engine instance container for the pipeline process singletons.

Owns the lazy construction of the chunks_q + EmbedConsumer + ProducerContext + concurrency
gates + JobLane, the startup health checks (orphan-chunk GC + Job Lane crash recovery +
ConnectorJobWatcher), and the per-object finalize hook driven by the EmbedConsumer's success
callback.

The Object Lane (per-object producers) and the Job Lane (directory summaries) both emit into
the same chunks_q; one EmbedConsumer drains it across all connector jobs and fires the
finalize hook, which writes the `objects` row + flips the `object_tasks` status. `pump` is
the non-blocking producer entry; `stash_finalize` holds the per-object context the hook pops
when the task's chunks land.

Core invariant (docs/ingest-pipeline.md): a chunk exists in Milvus iff a committed `objects`
row points at it (dir_summary is the deliberate exception). `_on_object_indexed` runs the
claim -> won-check -> delete-or-write sequence as one atomic body, so a cancel race can
neither leave an orphan chunk nor commit a row for a cancelled task.
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import time
from typing import AsyncIterator, cast

from ..config import ServerConfig
from ..storage.ids import chunk_id
from .adapters import ArtifactStoreAdapter, EmbedderAdapter, MilvusSinkAdapter, TxCacheAdapter
from .components.artifact_cache import ArtifactCacheService
from .components.connector_factory import ConnectorFactory
from .components.object_repository import ObjectRepository, TaskStatus
from .infra import InfraStack
from .job_lane import build_job_lane
from .job_watcher import ConnectorJobWatcher
from .pipeline import _EMBED_FLUSH_IDLE_MS, EmbedConsumer, TaskEnvelope, make_chunks_q
from .producers import select_producer
from .producers.base import (
    DescriptionConcurrencyGate,
    EndOfTask,
    ObjectTask,
    ProducedItem,
    ProducerContext,
    SummaryConcurrencyGate,
    cap_content,
)

logger = logging.getLogger(__name__)

# Bound plugin.connect() in the worker so a hanging/unreachable connector fails its job
# cleanly instead of blocking recovery forever. Mirrors the same-named constant in engine.py.
_WORKER_CONNECT_TIMEOUT_S = 30


class PipelineEmbedConsumer(EmbedConsumer):
    """EmbedConsumer wired for the real Milvus + embedding cache: supplies the embed cache key
    (provider/model/version aware, shared with CachingEmbeddingClient) and the Milvus row shape
    (chunk_id PK + namespace_id + indexed_at)."""

    def __init__(self, *args, namespace_id: str, embed_key_fn, **kwargs):
        super().__init__(*args, **kwargs)
        self._ns = namespace_id
        self._embed_key_fn = embed_key_fn

    def _cache_key(self, chunk) -> str:
        return self._embed_key_fn(chunk.content)

    def _build_row(self, env: TaskEnvelope, chunk, vec: list[float]) -> dict:
        content, _ = cap_content(chunk.content)
        loc = chunk.locator
        return {
            "chunk_id": chunk_id(self._ns, env.connector_uri, env.task_uri, chunk.chunk_kind, loc),
            "namespace_id": self._ns,
            "connector_uri": env.connector_uri,
            "object_uri": env.task_uri,
            "locator": loc,
            "content": content,
            "dense_vec": vec,
            "chunk_kind": chunk.chunk_kind,
            "metadata": chunk.metadata,
            "indexed_at": int(time.time() * 1000),
        }


class PipelineSupervisor:
    """per-Engine instance lazy pipeline singleton (NOT a module global - each ``Engine(cfg)``
    gets its own, so multi-instance / multi-namespace tests stay isolated).

    Owns the chunks_q + EmbedConsumer + ProducerContext + JobLane + Watcher + the
    ``_pending_finalize`` mutable dict. Core invariant: a chunk exists in Milvus iff a
    committed ``objects`` row points at it (dir_summary is the deliberate exception).
    """

    # okinds always routed to the producer + chunks_q + EmbedConsumer path (§3.2). image and
    # table_schema route conditionally (see routes_to_pipeline); everything else is
    # metadata-only. dir_summary is not an object_task - the Job Lane owns it (§3.5).
    _PIPELINE_OKINDS = (
        "document",
        "code",
        "text_blob",
        "message_stream",
        "record_collection",
        "table_rows",
    )

    def __init__(
        self,
        cfg: ServerConfig,
        infra: InfraStack,
        artifacts: ArtifactCacheService,
        objects: ObjectRepository,
        factory: ConnectorFactory,
    ) -> None:
        self._cfg = cfg
        self._infra = infra
        self._art = artifacts
        self._obj = objects
        self._factory = factory
        self._ns = cfg.namespace
        # --- process singletons (built lazily in _build_pipeline) ---
        self._chunks_q: asyncio.Queue | None = None
        self.embed_consumer: PipelineEmbedConsumer | None = None
        self.producer_ctx: ProducerContext
        # full_uri -> (cid, relpath, stat, indexable, plugin, task_id) for pipeline-path objects
        # whose objects-table row + object_tasks status are written by _on_object_indexed
        # when the EmbedConsumer reports the task done.
        self._pending_finalize: dict[str, tuple] = {}
        self._embed_idle_ms = _EMBED_FLUSH_IDLE_MS
        # Job Lane (dir_summary lane); built in _build_pipeline, inert when summary off.
        self.job_lane = None
        # ConnectorJobWatcher: out-of-band job completion / cancel / job-lane-evict (§5.7).
        self.job_watcher = None
        self.job_watcher_task: asyncio.Task | None = None

    # --- public API (Engine reaches via self.pipeline.xxx) ---

    def routes_to_pipeline(self, okind: str) -> bool:
        """Whether this okind goes through the producer -> chunks_q -> EmbedConsumer path.

        document / code / text_blob / message_stream / record_collection / table_rows always
        route (_PIPELINE_OKINDS). image routes only when [description] is enabled - its
        ImageChunksProducer makes a VLM call, so with it off the image is recorded metadata-only.
        table_schema routes only when [summary] is enabled - its TableSchemaProducer makes a
        summary LLM call; with it off the schema is metadata-only. dir_summary is not an
        object_task at all - the Job Lane owns it (§3.5)."""
        if okind in self._PIPELINE_OKINDS:
            return True
        if okind == "image":
            return self._cfg.description.enabled
        if okind == "table_schema":
            return self._infra.summary.enabled
        return False

    def stash_finalize(self, full_uri: str, ctx: tuple) -> None:
        """Stash the per-object finalize context for a pipeline-path object (the write site
        formerly at engine.py ``_index_object``). The EmbedConsumer success hook pops it when
        the task's chunks land. Encapsulated as a method so the mutable dict is owned here and
        never touched from outside."""
        self._pending_finalize[full_uri] = ctx

    async def pump(
        self, plugin, connector_uri: str, relpath: str, full_uri: str, okind: str, task: dict
    ) -> None:
        """Produce this object's chunks and forward them to the shared chunks_q, then return -
        a non-blocking producer pump. Completion is async: the EmbedConsumer writes the chunks
        (delete_by_object once on the first chunk, then upsert - the §6.1 per-object atomic
        invariant) and fires the success hooks, which write the objects row + flip the
        object_tasks status. The caller marks nothing for this task ('deferred')."""
        if self.embed_consumer is None:
            self._build_pipeline()
        # _build_pipeline set self._chunks_q; pull a local so the type checker carries the
        # non-None guarantee across the awaits below (self._chunks_q is typed Optional, and
        # Pyright does not narrow an instance attribute across the method-call boundary).
        chunks_q = self._chunks_q
        assert chunks_q is not None
        task_id = task["id"]
        job_id = task.get("connector_job_id")
        producer = select_producer(okind, self.producer_ctx)
        try:
            if producer is None:
                # unreachable for routed okinds; emit a bare EndOfTask so the consumer finalizes
                # (zero-chunk) and the success hook still writes the metadata-only objects row.
                await chunks_q.put(
                    TaskEnvelope(task_id, full_uri, connector_uri, job_id, EndOfTask())
                )
            else:
                otask = ObjectTask(
                    object_uri=relpath,
                    connector_uri=connector_uri,
                    okind=okind,
                    change_kind=task["change_kind"],
                    connector_job_id=job_id,
                    task_id=task_id,
                    plugin=plugin,
                    ocfg=plugin.ctx.object_config_for(relpath),
                )
                # ChunksProducer.produce is declared ``async def -> AsyncIterator`` but the
                # implementations are async generators (``async def`` + ``yield``), so a call
                # returns an AsyncGenerator, not a coroutine - iterate directly, no ``await``.
                # The cast only pacifies the type checker; it is a no-op at runtime.
                chunks = cast(AsyncIterator[ProducedItem], cast(object, producer.produce(otask)))
                async for item in chunks:
                    await chunks_q.put(TaskEnvelope(task_id, full_uri, connector_uri, job_id, item))
        except BaseException:
            # produce() failed or was cancelled before its EndOfTask landed: the consumer
            # only finalizes a task whose EndOfTask it saw (pipeline.py _maybe_finalize), so
            # the stashed finalize context would otherwise leak and could later write an
            # objects row for a dead task. Drop it, then re-raise so _process_with_retry
            # sees the failure / cancel. BaseException (not Exception) so CancelledError
            # is covered too - the cleanup is a non-blocking dict.pop, and we re-raise.
            self._pending_finalize.pop(full_uri, None)
            raise
        finally:
            if okind == "message_stream":
                # GC the per-task raw_records jsonl the MessageStreamProducer materialized
                # (§5.4): only needed to regroup messages by thread during produce(), which is
                # done once the produce loop above exhausts. Runs on success AND failure.
                try:
                    await asyncio.to_thread(
                        self._infra.artifact_cache.delete_artifact,
                        self._ns,
                        full_uri,
                        "raw_records",
                    )
                except Exception as e:  # noqa: BLE001 - GC of a temp artifact must never fail the task
                    logger.exception(
                        "message_stream raw_records GC failed for %s; ignoring", full_uri, e
                    )

    # --- lifecycle ---

    async def startup(self) -> None:
        """Build the process singletons, run the startup reconcile, and start the watcher
        (formerly Engine.startup steps 2-6)."""
        self._build_pipeline()
        await self._gc_orphan_chunks()
        await self._recover_job_lane()
        # ConnectorJobWatcher runs in this same event loop as the EmbedConsumer + SummaryWorker
        # pool, finalizing jobs out-of-band (§5.7).
        self.job_watcher = ConnectorJobWatcher(self._infra.meta, self.job_lane)
        self.job_watcher_task = asyncio.create_task(self.job_watcher.run())

    async def shutdown(self) -> None:
        """Stop the watcher + Job Lane + EmbedConsumer in their original order (formerly
        Engine.shutdown steps 1-3). Infra (meta + tx_cache) is closed separately by Engine."""
        if self.job_watcher is not None:
            self.job_watcher.stop()
            if self.job_watcher_task is not None:
                try:
                    await self.job_watcher_task
                except asyncio.CancelledError:
                    pass  # expected during shutdown
                except Exception as e:  # noqa: BLE001 - shutdown must not abort on a watcher crash
                    logger.exception(
                        "ConnectorJobWatcher task ended with an error: %s; ignoring", e
                    )
            self.job_watcher = None
        if self.job_lane is not None:
            # stop the SummaryWorker pool first so no new dir chunks are produced, then
            # drain whatever already reached the queue.
            await self.job_lane.stop()
        if self.embed_consumer is not None:
            # drain the queue + flush the final batch before the loop closes, so an
            # in-flight task's chunks aren't lost on shutdown.
            await self.embed_consumer.shutdown()
            self.embed_consumer = None

    # --- EmbedConsumer finalize hook (registered via register_on_succeeded; signature fixed) ---

    async def _on_object_indexed(
        self,
        task_uri: str,
        job_id: str | None,
        chunk_count: int = 0,
        partial: bool = False,
        error: str | None = None,
    ) -> None:
        """EmbedConsumer finalize hook: write the `objects` row and set the object_tasks status
        for a pipeline-path object - now that the EmbedConsumer knows its final chunk_count +
        partial flag. When `error` is set the flush dropped this task's chunks: record it failed
        (objects.search_status='failed', object_tasks.last_error) and do NOT advance the
        connector cursor, so a later sync can retry. Skips tasks it has no stashed context for
        (e.g. a Job Lane directory_summary success, which has no objects row).

        Atomic body (engine-redesign.md §5 / D8): claim->won-check->delete-or-write stays in
        this one method. Splitting it would break the orphan-chunk invariant under cancel races."""
        ctx = self._pending_finalize.pop(task_uri, None)
        if ctx is None:
            return
        cid, connector_uri, relpath, st, indexable, plugin, task_id = ctx
        if error is not None:
            # Embed/upsert failed for this object. Record it failed and leave the cursor where
            # it was (on_object_indexed not called) so the object isn't treated as committed.
            await self._obj.write_object_row(cid, relpath, st, indexable, "failed", chunk_count)
            await self._obj.advance_task(
                task_id, TaskStatus.FAILED, from_status=TaskStatus.RUNNING, error=str(error)
            )
            return
        if chunk_count == 0:
            search_status = "not_indexed"
        elif partial:
            search_status = "partial"
        else:
            search_status = "indexed"
        # Claim completion FIRST, guarded on status='running'. Completion lives here, not in
        # the worker loop: the pump enqueues chunks without blocking, so only the consumer
        # knows when they have landed.
        won = await self._obj.advance_task(
            task_id, TaskStatus.SUCCEEDED, from_status=TaskStatus.RUNNING
        )
        if won == 0:
            # The task was cancelled out from under us while its chunks were embedding (an
            # external `job cancel`, or a connector removal racing the shared consumer). We
            # have already upserted this object's chunks to Milvus and no objects row will
            # ever point at them - but search queries Milvus directly, so they would survive
            # as orphan hits (un-inspectable, un-cat-able, yet matchable). Reconcile by
            # deleting them, leaving the cursor untouched so a later sync re-does the object
            # cleanly. No-op when chunk_count == 0.
            await asyncio.to_thread(
                self._infra.milvus.delete_by_object, self._ns, connector_uri, task_uri
            )
            return
        await self._obj.write_object_row(cid, relpath, st, indexable, search_status, chunk_count)
        await plugin.on_object_indexed(relpath)

    # --- internal assembly / health checks (migrated verbatim) ---

    def _build_pipeline(self) -> None:
        """Construct the process-level chunks_q + EmbedConsumer + ProducerContext and start
        the consumer draining in the background. Idempotent; called from startup() (and
        lazily from pump so the pipeline path works even if a caller skipped startup). The
        EmbedConsumer is shared across all jobs so embed batches fill across connectors
        (§5.2)."""
        if self.embed_consumer is not None:
            return
        batch_size = self._cfg.embedding.batch_size
        # Build into locals, assign to the instance fields last: self.embed_consumer /
        # self._chunks_q are typed Optional (None until built), and Pyright does not narrow an
        # instance attribute across the intervening self.* writes + function calls below, so
        # register_on_succeeded / start would flag as "member of None". A local stays narrowed.
        chunks_q = make_chunks_q(batch_size)
        consumer = PipelineEmbedConsumer(
            # raw provider embed (no caching) so the consumer's TxCacheAdapter is the single
            # embed cache and there is no double-cache; cache key matches CachingEmbeddingClient.
            EmbedderAdapter(self._infra.embed.embed_api),
            MilvusSinkAdapter(self._infra.milvus, self._ns),
            TxCacheAdapter(
                self._infra.tx_cache,
                kind="embedding",
                provider=self._infra.embed.provider_name,
                model=self._infra.embed.model,
                version=self._infra.embed.version,
            ),
            batch_size,
            idle_ms=self._embed_idle_ms,
            namespace_id=self._ns,
            embed_key_fn=self._infra.embed.key,
        )
        consumer.register_on_succeeded(self._on_object_indexed)
        # ONE description gate + ONE summary gate per process (§5.5), shared by BOTH the Map
        # producers (image / table_schema) and the Job Lane SummaryWorker pool, so every VLM /
        # summary provider call - wherever it originates - draws from the same in-flight budget
        # ([description].concurrency / [summary].concurrency).
        self._description_gate = DescriptionConcurrencyGate(self._cfg.description.concurrency)
        self._summary_gate = SummaryConcurrencyGate(self._cfg.summary.concurrency)
        self.producer_ctx = ProducerContext(
            cfg=self._cfg,
            namespace_id=self._ns,
            artifacts=ArtifactStoreAdapter(
                self._art.put_artifact,
                self._art.read_artifact,
                self._infra.artifact_cache.artifact_path,
                self._art.read_artifact_fresh,
            ),
            converter=self._infra.converter,
            vlm=self._infra.vlm,
            summary=self._infra.summary,
            description_gate=self._description_gate,
            summary_gate=self._summary_gate,
        )
        # Job Lane (§3.5): dir summaries emit into the SAME chunks_q. Its on_embed_succeeded
        # is registered alongside the Object Lane per-task hook; it ignores file successes
        # (files don't gate a dir) and counts a persisted directory_summary toward completion.
        self.job_lane = build_job_lane(
            self._cfg,
            tx_cache=self._infra.tx_cache,
            summary=self._infra.summary,
            vlm=self._infra.vlm,
            converter=self._infra.converter,
            chunks_q=chunks_q,
            artifacts=self.producer_ctx.artifacts,
            namespace_id=self._ns,
            description_gate=self._description_gate,
            summary_gate=self._summary_gate,
        )
        consumer.register_on_succeeded(self.job_lane.on_embed_succeeded)
        consumer.start(chunks_q)
        self.job_lane.start()  # no-op unless cfg.summary.enabled
        # Publish only once fully wired - the idempotent guard above reads self.embed_consumer,
        # and pump() reads self._chunks_q, so both must be set before this method returns.
        self._chunks_q = chunks_q
        self.embed_consumer = consumer

    async def _gc_orphan_chunks(self) -> int:
        """Startup reconcile: delete Milvus chunks that no committed `objects` row points at.

        Orphans arise only in narrow windows - a consumer crash between chunk upsert and the
        finalize hook, or chunks left before the per-object self-heal landed (see
        _on_object_indexed). They are invisible to ls/inspect/cat (no objects row)
        yet still match search, which queries Milvus directly.

        Cost-guarded so a healthy index pays almost nothing: compare the Milvus row total
        against the sum of committed chunk_counts (both scoped to non-summary chunks -
        directory summaries have no objects row). They match unless there is genuine excess,
        so the full distinct-object scan + per-object delete runs only when orphans exist."""
        try:
            total = await asyncio.to_thread(
                self._infra.milvus.count, self._ns, self._infra.milvus.GC_SCOPE_EXPR
            )
            if total == 0:
                return 0
            conns = await self._obj.list_connectors_summary()
            expected = 0
            valid: set[str] = set()
            for c in conns:
                for r in await self._obj.list_objects_with_chunks(c["id"]):
                    expected += int(r["chunk_count"])
                    valid.add(c["root_uri"] + r["object_uri"])
            if total <= expected:
                return 0  # healthy (or under-indexed, not an orphan case) - stop after the counts
            # genuine excess: enumerate non-summary objects and drop those with no committed row
            present = await asyncio.to_thread(self._infra.milvus.distinct_objects, self._ns)
            deleted = 0
            for connector_uri, object_uri in present:
                if object_uri not in valid:
                    await asyncio.to_thread(
                        self._infra.milvus.delete_by_object, self._ns, connector_uri, object_uri
                    )
                    deleted += 1
            if deleted:
                logger.info("startup GC purged %d orphan object(s) from the index", deleted)
            return deleted
        except Exception as e:  # noqa: BLE001
            # Best-effort housekeeping; never block startup on it.
            logger.warning("startup orphan GC skipped (%s)", e)
            return 0

    async def _recover_job_lane(self) -> None:
        """Rebuild the Job Lane's in-memory dir trees for jobs left 'running' by a
        crash (§6.4.5). Best-effort: a per-job failure is logged and skipped, never blocking
        boot. Already-written directory summaries are recomputed (idempotent + summary-cache
        cheap) rather than queried back from Milvus."""
        if self.job_lane is None or not self.job_lane.enabled:
            return

        try:
            jobs = await self._obj.list_running_jobs()
        except Exception as e:  # noqa: BLE001 - recovery must never wedge startup
            logger.exception(
                "Job Lane recovery aborted while listing running jobs: %s; skipping", e
            )
            return
        for job in jobs:
            job_id, cid = job["id"], job["connector_id"]
            try:
                crow = await self._obj.get_connector_root_type_config(cid)
                if not crow:
                    continue
                connector_uri, ctype = crow["root_uri"], crow["type"]
                stored_cfg = _json.loads(crow["config_json"]) if crow["config_json"] else {}
                plugin = self._factory.build_plugin(ctype, stored_cfg, cid).plugin
                await asyncio.wait_for(plugin.connect(), timeout=_WORKER_CONNECT_TIMEOUT_S)
                rows = await self._obj.list_job_tasks_excluding_dir_summary(job_id)
                objects = [
                    (r["object_uri"], plugin.object_kind_of(r["object_uri"]), r["status"])
                    for r in rows
                ]
                self.job_lane.recover_job(job_id, connector_uri, plugin, objects, [])
            except Exception as e:  # noqa: BLE001
                logger.warning("Job Lane recovery for job %s failed: %s", job_id, e)
