"""PipelineSupervisor - per-Engine container for the pipeline process singletons.

Owns lazy construction of chunks_q + EmbedConsumer + ProducerContext + concurrency
gates + JobLane, startup health checks, and the per-object finalize hook.

Object Lane and Job Lane both emit into the same chunks_q; one EmbedConsumer drains it
and fires the finalize hook, which writes the `objects` row and flips `object_tasks`
status. `pump` is the non-blocking producer entry; `stash_finalize` holds the per-object
context the hook pops when the task's chunks land.

Core invariant (docs/ingest-pipeline.md): a chunk exists in Milvus iff a committed
`objects` row points at it (dir_summary is the deliberate exception).
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

# Mirrors the constant in engine.py: bound plugin.connect() in workers.
_WORKER_CONNECT_TIMEOUT_S = 30


class PipelineEmbedConsumer(EmbedConsumer):
    """EmbedConsumer wired for real Milvus + the shared embedding cache."""

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
    """Per-Engine lazy pipeline singleton (not a module global).

    Keeps multi-instance / multi-namespace tests isolated while sharing the
    EmbedConsumer across connector jobs for better batching.
    """

    # okinds always routed through the pipeline. image/table_schema route conditionally;
    # dir_summary is owned by the Job Lane, not object_tasks.
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
        self._chunks_q: asyncio.Queue | None = None
        self.embed_consumer: PipelineEmbedConsumer | None = None
        self.producer_ctx: ProducerContext | None = None
        # full_uri -> finalize context for objects awaiting _on_object_indexed.
        self._pending_finalize: dict[str, tuple] = {}
        self._embed_idle_ms = _EMBED_FLUSH_IDLE_MS
        self.job_lane = None
        self.job_watcher = None
        self.job_watcher_task: asyncio.Task | None = None

    # --- public API ---

    def routes_to_pipeline(self, okind: str) -> bool:
        """Whether this okind goes through producer -> chunks_q -> EmbedConsumer."""
        if okind in self._PIPELINE_OKINDS:
            return True
        if okind == "image":
            return self._cfg.description.enabled
        if okind == "table_schema":
            return self._infra.summary.enabled
        return False

    def stash_finalize(self, full_uri: str, ctx: tuple) -> None:
        """Stash per-object context for the EmbedConsumer success hook."""
        self._pending_finalize[full_uri] = ctx

    async def pump(
        self, plugin, connector_uri: str, relpath: str, full_uri: str, okind: str, task: dict
    ) -> None:
        """Produce chunks and enqueue them; completion is async via the consumer hook."""
        if self.embed_consumer is None:
            self._build_pipeline()
        chunks_q = self._chunks_q
        assert chunks_q is not None
        task_id = task["id"]
        job_id = task.get("connector_job_id")
        producer = select_producer(okind, self.producer_ctx)
        try:
            if producer is None:
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
                # async generator: iterate directly, do not await the call.
                chunks = cast(AsyncIterator[ProducedItem], cast(object, producer.produce(otask)))
                async for item in chunks:
                    await chunks_q.put(TaskEnvelope(task_id, full_uri, connector_uri, job_id, item))
        except BaseException:
            # Producer failed or was cancelled before EndOfTask: drop stashed context so
            # a dead task cannot later commit an objects row.
            self._pending_finalize.pop(full_uri, None)
            raise
        finally:
            if okind == "message_stream":
                try:
                    await asyncio.to_thread(
                        self._infra.artifact_cache.delete_artifact,
                        self._ns,
                        full_uri,
                        "raw_records",
                    )
                except Exception as e:  # noqa: BLE001 - temp artifact GC must not fail task
                    logger.exception(
                        "message_stream raw_records GC failed for %s; ignoring", full_uri, e
                    )

    # --- lifecycle ---

    async def startup(self) -> None:
        """Build singletons, run startup reconcile, and start the watcher."""
        self._build_pipeline()
        await self._gc_orphan_chunks()
        await self._recover_job_lane()
        self.job_watcher = ConnectorJobWatcher(self._infra.meta, self.job_lane)
        self.job_watcher_task = asyncio.create_task(self.job_watcher.run())

    async def shutdown(self) -> None:
        """Stop watcher, Job Lane, and EmbedConsumer in order."""
        if self.job_watcher is not None:
            self.job_watcher.stop()
            if self.job_watcher_task is not None:
                try:
                    await self.job_watcher_task
                except asyncio.CancelledError:
                    pass
                except Exception as e:  # noqa: BLE001 - shutdown must not abort on watcher crash
                    logger.exception(
                        "ConnectorJobWatcher task ended with an error: %s; ignoring", e
                    )
            self.job_watcher = None
        if self.job_lane is not None:
            await self.job_lane.stop()
        if self.embed_consumer is not None:
            await self.embed_consumer.shutdown()
            self.embed_consumer = None

    # --- finalize hook ---

    async def _on_object_indexed(
        self,
        task_uri: str,
        job_id: str | None,
        chunk_count: int = 0,
        partial: bool = False,
        error: str | None = None,
    ) -> None:
        """Write the `objects` row and flip object_tasks status after chunks land.

        Keeps the claim -> won-check -> delete-or-write sequence in one method so a
        cancel race can neither leave an orphan chunk nor commit a row for a cancelled
        task.
        """
        ctx = self._pending_finalize.pop(task_uri, None)
        if ctx is None:
            return
        cid, connector_uri, relpath, st, indexable, plugin, task_id = ctx
        if error is not None:
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
        won = await self._obj.advance_task(
            task_id, TaskStatus.SUCCEEDED, from_status=TaskStatus.RUNNING
        )
        if won == 0:
            # Cancelled while embedding: chunks already upserted but no objects row will
            # point at them, so delete to avoid orphan hits.
            await asyncio.to_thread(
                self._infra.milvus.delete_by_object, self._ns, connector_uri, task_uri
            )
            return
        await self._obj.write_object_row(cid, relpath, st, indexable, search_status, chunk_count)
        await plugin.on_object_indexed(relpath)

    # --- internal assembly / health checks ---

    def _build_pipeline(self) -> None:
        """Construct and wire chunks_q + EmbedConsumer + ProducerContext + JobLane."""
        if self.embed_consumer is not None:
            return
        batch_size = self._cfg.embedding.batch_size
        chunks_q = make_chunks_q(batch_size)
        consumer = PipelineEmbedConsumer(
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
        # One gate per process shared by Map producers and the Job Lane SummaryWorker pool.
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
        # Publish only after full wiring so lazy callers see a consistent state.
        self._chunks_q = chunks_q
        self.embed_consumer = consumer

    async def _gc_orphan_chunks(self) -> int:
        """Delete Milvus chunks with no committed `objects` row."""
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
                return 0
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
        except Exception as e:  # noqa: BLE001 - best-effort housekeeping
            logger.warning("startup orphan GC skipped (%s)", e)
            return 0

    async def _recover_job_lane(self) -> None:
        """Rebuild Job Lane dir trees for jobs left running by a crash."""
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
