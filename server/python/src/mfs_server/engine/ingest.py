"""IngestOrchestrator: end-to-end execution of a sync ingest job.

Job pipeline:
    add -> open_sync_job -> drain_job -> run_job -> finalize_job -> commit cursor
          register          enumerate    map phase   terminal status
                            + dir tree   claim / retry / circuit-breaker / heartbeat

Per-object write (ObjectIndexer.handle):
    deleted      -> DeletedHandler                              -> None
    renamed      -> RenameHandler: reuse old vectors            -> None
                   (no old chunks: cleanup, then fall through)
    add/modify   -> stat + okind + indexable
                      +-- pipeline  -> stash_finalize + pump    -> "deferred"
                      +-- else      -> write "not_indexed"      -> None

Invariant: a chunk exists in Milvus iff a committed ``objects`` row points at it
(dir_summary is the deliberate exception).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import suppress
from typing import Protocol

from ..config import ServerConfig
from ..connectors.base import SyncOptions
from ..connectors.registry import get_plugin_cls
from ..storage.ids import chunk_id
from .components.artifact_cache import ArtifactCacheService
from .components.connector_factory import ConnectorFactory
from .components.object_repository import ObjectRepository, TaskStatus
from .infra import InfraStack
from .pipeline_supervisor import PipelineSupervisor

logger = logging.getLogger(__name__)

_HEARTBEAT_INTERVAL_S = 10  # refreshed this often (<< stale window)

# Per-object events that aren't systemic failures: the source vanished or its path
# type changed mid-sync. Not retried and not counted toward the circuit breaker.
_PER_OBJECT_SKIP_ERRORS: tuple = (
    FileNotFoundError,
    NotADirectoryError,
    IsADirectoryError,
)


def _normalize_json(s: str) -> str:
    """Sort-key + strip-whitespace normalize a JSON object string so two configs
    with identical contents but different key ordering / whitespace compare equal."""
    import json as _json

    try:
        return _json.dumps(_json.loads(s), sort_keys=True, separators=(",", ":"))
    except (ValueError, TypeError):
        return s


# --- single-object write: ObjectIndexer dispatch ---


class IndexContext:
    """Per-task write context. ``st`` / ``okind`` / ``indexable`` are filled by
    ``ObjectIndexer`` on the fallthrough path so handlers don't re-stat."""

    __slots__ = (
        "plugin",
        "connector_uri",
        "task",
        "ns",
        "relpath",
        "full_uri",
        "cid",
        "st",
        "okind",
        "indexable",
    )

    def __init__(self, plugin, connector_uri, task, ns, relpath, full_uri, cid):
        self.plugin = plugin
        self.connector_uri = connector_uri
        self.task = task
        self.ns = ns
        self.relpath = relpath
        self.full_uri = full_uri
        self.cid = cid
        self.st = None
        self.okind = None
        self.indexable = False


class IndexHandler(Protocol):
    async def handle(self, ctx: IndexContext) -> str | None:
        """None = synchronous completion (caller advances the task); 'deferred' =
        pipeline okind whose completion is flipped async by the EmbedConsumer hook."""


class DeletedHandler:
    def __init__(
        self, objects: ObjectRepository, artifacts: ArtifactCacheService, infra: InfraStack
    ):
        self._obj = objects
        self._art = artifacts
        self._infra = infra

    async def handle(self, ctx: IndexContext) -> str | None:
        await self._obj.delete_object_row(ctx.cid, ctx.relpath)
        await asyncio.to_thread(
            self._infra.milvus.delete_by_object, ctx.ns, ctx.connector_uri, ctx.full_uri
        )
        await self._art.drop_artifacts(ctx.ns, ctx.full_uri)
        await ctx.plugin.on_object_deleted(ctx.relpath)
        return None


class RenameHandler:
    def __init__(
        self, objects: ObjectRepository, artifacts: ArtifactCacheService, infra: InfraStack
    ):
        self._obj = objects
        self._art = artifacts
        self._infra = infra

    async def handle(self, ctx: IndexContext) -> str | None:
        old_full = ctx.connector_uri + ctx.task["old_uri"]
        # rename = chunk_id rewrite, reusing vectors (zero re-embed)
        old_chunks = await asyncio.to_thread(
            self._infra.milvus.get_chunks_by_object, ctx.ns, ctx.connector_uri, old_full
        )
        if old_chunks:
            rows = []
            for ch in old_chunks:
                loc = ch.get("locator")
                rows.append(
                    {
                        "chunk_id": chunk_id(
                            ctx.ns, ctx.connector_uri, ctx.full_uri, ch["chunk_kind"], loc
                        ),
                        "namespace_id": ctx.ns,
                        "connector_uri": ctx.connector_uri,
                        "object_uri": ctx.full_uri,
                        "locator": loc,
                        "content": ch["content"],
                        "dense_vec": ch["dense_vec"],
                        "chunk_kind": ch["chunk_kind"],
                        "metadata": ch.get("metadata") or {},
                        "indexed_at": ch.get("indexed_at") or int(time.time() * 1000),
                    }
                )
            await asyncio.to_thread(
                self._infra.milvus.delete_by_object, ctx.ns, ctx.connector_uri, old_full
            )
            await asyncio.to_thread(self._infra.milvus.upsert, ctx.ns, rows)
            await self._art.rename_artifacts(ctx.ns, old_full, ctx.full_uri)
            st = await ctx.plugin.stat(ctx.relpath)
            await self._obj.delete_object_row(ctx.cid, ctx.task["old_uri"])
            await self._obj.write_object_row(ctx.cid, ctx.relpath, st, True, "indexed", len(rows))
            await ctx.plugin.on_object_indexed(ctx.relpath)
            return None  # reused vectors - no chunk/embed
        # old had no chunks: drop old refs, fall through to index new normally
        await asyncio.to_thread(
            self._infra.milvus.delete_by_object, ctx.ns, ctx.connector_uri, old_full
        )
        await self._obj.delete_object_row(ctx.cid, ctx.task["old_uri"])
        return "continue"  # fall through to pipeline/metadata


class PipelineIndexHandler:
    def __init__(self, pipeline: PipelineSupervisor):
        self._pipeline = pipeline

    async def handle(self, ctx: IndexContext) -> str | None:
        # Pipeline okind: chunks are produced + embedded + upserted asynchronously by the
        # shared EmbedConsumer; the objects row + on_object_indexed are written by its
        # success hook, so stash the per-object context and return 'deferred'.
        self._pipeline.stash_finalize(
            ctx.full_uri,
            (
                ctx.cid,
                ctx.connector_uri,
                ctx.relpath,
                ctx.st,
                ctx.indexable,
                ctx.plugin,
                ctx.task["id"],
            ),
        )
        await self._pipeline.pump(
            ctx.plugin, ctx.connector_uri, ctx.relpath, ctx.full_uri, ctx.okind, ctx.task
        )
        # 'deferred': the caller must not mark this task succeeded yet - its chunks
        # aren't written until the EmbedConsumer hook fires.
        return "deferred"


class MetadataOnlyHandler:
    def __init__(self, objects: ObjectRepository, infra: InfraStack):
        self._obj = objects
        self._infra = infra

    async def handle(self, ctx: IndexContext) -> str | None:
        # No chunks produced (binary / opted-out / emptied): still purge chunks from a
        # previous index so search doesn't return stale content.
        await asyncio.to_thread(
            self._infra.milvus.delete_by_object, ctx.ns, ctx.connector_uri, ctx.full_uri
        )
        await self._obj.write_object_row(
            ctx.cid, ctx.relpath, ctx.st, ctx.indexable, "not_indexed", 0
        )
        await ctx.plugin.on_object_indexed(ctx.relpath)
        return None


class ObjectIndexer:
    """Routes one object_task to its write handler: deleted -> renamed (reuse or
    fallthrough) -> pipeline ('deferred') -> metadata-only."""

    def __init__(
        self,
        objects: ObjectRepository,
        artifacts: ArtifactCacheService,
        infra: InfraStack,
        pipeline: PipelineSupervisor,
        ns: str,
    ):
        self._obj = objects
        self._art = artifacts
        self._infra = infra
        self._pipeline = pipeline
        self._ns = ns
        self._deleted = DeletedHandler(objects, artifacts, infra)
        self._renamed = RenameHandler(objects, artifacts, infra)
        self._pipeline_idx = PipelineIndexHandler(pipeline)
        self._metadata = MetadataOnlyHandler(objects, infra)

    async def handle(self, plugin, connector_uri: str, task: dict) -> str | None:
        """Handle one object_task. Returns None on synchronous completion or
        'deferred' for pipeline okinds (completed async by the EmbedConsumer)."""
        ctx = IndexContext(
            plugin=plugin,
            connector_uri=connector_uri,
            task=task,
            ns=self._ns,
            relpath=task["object_uri"],
            full_uri=connector_uri + task["object_uri"],
            cid=task["connector_id"],
        )
        kind = task["change_kind"]
        if kind == "deleted":
            return await self._deleted.handle(ctx)
        if kind == "renamed" and task["old_uri"]:
            r = await self._renamed.handle(ctx)
            if r is None:
                return None  # reused old vectors, whole object done
            # old_chunks empty: cleaned up old refs, fall through
        # shared preamble for add / modify / renamed-empty-fallback
        ctx.st = await plugin.stat(ctx.relpath)
        ctx.okind = plugin.object_kind_of(ctx.relpath)
        top_cfg = plugin.ctx.object_config_for(ctx.relpath)
        # indexable: binary okinds are never indexed; [[objects]] can opt out any
        # okind (record it for ls/inspect, skip chunk/embed/Milvus).
        ctx.indexable = ctx.okind not in ("binary",) and top_cfg.indexable
        # Directory summaries are built by the Job Lane from the dir tree, not here.
        if ctx.indexable and self._pipeline.routes_to_pipeline(ctx.okind):
            return await self._pipeline_idx.handle(ctx)
        return await self._metadata.handle(ctx)


class IngestOrchestrator:
    """End-to-end execution of a sync ingest job: register -> open job -> enumerate
    -> map-phase process -> single-object write -> finalize -> cancel."""

    def __init__(
        self,
        cfg: ServerConfig,
        infra: InfraStack,
        factory: ConnectorFactory,
        pipeline: PipelineSupervisor,
        objects: ObjectRepository,
        artifacts: ArtifactCacheService,
    ) -> None:
        self._cfg = cfg
        self._infra = infra
        self._factory = factory
        self._pipeline = pipeline
        self._obj = objects
        self._art = artifacts
        self._ns = cfg.namespace
        self._remove_connector = None  # back-filled by bind_remover (add-failure rollback)
        self._indexer = ObjectIndexer(objects, artifacts, infra, pipeline, self._ns)

    def bind_remover(self, remove_connector) -> None:
        """Inject the ``remove_connector`` callable for ``add``'s failure rollback."""
        self._remove_connector = remove_connector

    # --- add (register + sync + worker) ---

    async def add(
        self,
        target: str,
        config: dict | None = None,
        full: bool = False,
        since: str | None = None,
        process: bool = True,
        update_config: bool = False,
    ) -> str:
        """Register + sync + enqueue tasks. process=True runs the job inline and
        returns when done; process=False leaves it 'queued' for a standalone worker.
        On an already-registered connector, --config is ignored unless update_config."""
        import json

        r = self._factory.resolve_target(target)
        _, connector_uri, ctype, default_config = r.ctype, r.connector_uri, r.scheme, r.config
        # Reject --since on connectors that don't honor it (since_pushdown), rather
        # than silently full-scanning.
        if since:
            cls = get_plugin_cls(ctype)
            if cls is not None and not getattr(cls.CAPABILITIES, "since_pushdown", False):
                raise ValueError("since_unsupported")
        # User config overrides resolved defaults (keeps auto {root, client_id}).
        cfg_dict = {**default_config, **config} if config is not None else default_config
        # Validate config before connecting so bad fields are a clean error, not a
        # raw exception deep in connect()/read().
        self._factory.validate_config(ctype, cfg_dict)
        existing_connector = await self._obj.get_connector_id_by_uri(connector_uri)
        cid = await self.register_or_get_connector(
            connector_uri,
            ctype,
            cfg_dict,
            overwrite_config=update_config,
            config_explicit=config is not None,
        )
        row0 = await self._obj.get_connector_config_and_status(cid)
        if row0 and row0["status"] == "removing":
            raise ValueError("connector_removing")
        # Session uses the caller's raw config; persisted copy is redacted. Workers
        # resolve secrets from credential_ref, so persistent runs need credential_ref.
        stored_cfg = (
            cfg_dict
            if config is not None
            else (json.loads(row0["config_json"]) if row0 and row0["config_json"] else cfg_dict)
        )

        job_id = await self.open_sync_job(cid, process)
        try:
            return await self.drain_job(
                job_id, cid, connector_uri, ctype, stored_cfg, full, since, process
            )
        except Exception:
            if existing_connector is None:
                with suppress(Exception):
                    await self._remove_connector(connector_uri)
            raise

    async def register_or_get_connector(
        self,
        connector_uri: str,
        ctype: str,
        config: dict,
        overwrite_config: bool = False,
        config_explicit: bool = True,
    ) -> str:
        import json

        # Reject plaintext secrets before redact(): a rejected literal can't round-trip
        # into a stored placeholder mistaken for a real credential.
        self._factory.validate_credentials(config)
        stored = self._factory.redact(config)
        row = await self._obj.get_connector_id_and_config_by_uri(connector_uri)
        if row:
            # Re-register an existing connector: refresh stored config on drift.
            # Existing chunks keep the OLD config until re-synced with --force-index.
            new_json = json.dumps(stored, sort_keys=True)
            old_json = row["config_json"] or "{}"
            drift = _normalize_json(new_json) != _normalize_json(old_json)
            if drift and not config_explicit:
                # --config omitted: `config` is a URI-derived default, not the caller's
                # intent. Refuse rather than silently drop stored config.
                raise ValueError("config_required")
            if overwrite_config or drift:
                await self._obj.update_connector_config(row["id"], json.dumps(stored))
                if drift and not overwrite_config:
                    # Count at-risk objects so the warning is concrete.
                    indexed = await self._obj.count_indexed_objects(row["id"])
                    logger.warning(
                        "--config differs from stored config for %s; persisted, but %d "
                        "existing indexed object(s) retain the OLD config until you re-sync "
                        "with `mfs add --force-index`.",
                        connector_uri,
                        indexed,
                    )
            return row["id"]
        cid = uuid.uuid4().hex
        await self._obj.insert_connector(cid, connector_uri, ctype, json.dumps(stored))
        return cid

    async def open_sync_job(self, cid: str, process: bool) -> str:
        """Reserve the one-in-flight-sync slot for a connector and inherit its
        leftover tasks. Raises connector_removing / sync_already_running. Callers
        that mutate state MUST call this before mutating, so a rejected sync leaves
        nothing half-applied."""
        return await self._obj.open_sync_job(cid, process)

    async def drain_job(
        self,
        job_id: str,
        cid: str,
        connector_uri: str,
        ctype: str,
        stored_cfg: dict,
        full: bool,
        since: str | None,
        process: bool,
    ) -> str:
        """Run a reserved sync job: enumerate (plugin.sync) -> enqueue object_tasks ->
        process inline (process=True) or leave queued for a worker."""
        plugin = None
        ctx = None
        aborted: str | None = None
        try:
            # build/connect/enumerate are inside the try: on failure the job must be
            # finalized 'failed' or its slot would block the connector's next sync.
            built = self._factory.build_plugin(ctype, stored_cfg, cid)
            plugin, ctx = built.plugin, built.ctx
            await plugin.connect()
            opts = SyncOptions(full=full, since=since)
            # Heartbeat during enumeration too, else a slow connector looks stale and
            # gets reclaimed mid-enumeration.
            stop_hb = asyncio.Event()
            hb = asyncio.create_task(self.heartbeat_loop(job_id, stop_hb))
            # Build this job's in-memory dir tree as sync() yields.
            self._pipeline.job_lane.register_job(job_id, connector_uri, plugin)
            try:
                async for ch in plugin.sync(opts):
                    if ch.kind == "deleted" and (
                        ctx.enumeration_mode == "incremental"
                        or getattr(plugin.CAPABILITIES, "delete_detection", "") == "never"
                    ):
                        # Skip deletes in incremental mode or for never-delete connectors.
                        continue
                    tid = uuid.uuid4().hex
                    # User [[objects]] priority overrides the connector default.
                    user_priority = plugin.ctx.object_config_for(ch.uri).priority
                    await self._obj.insert_task(
                        tid,
                        job_id,
                        cid,
                        ch.uri,
                        ch.old_uri,
                        ch.kind,
                        user_priority if user_priority is not None else plugin.task_priority(ch),
                    )
                    if ch.kind != "deleted":
                        # Register pipeline okinds into the dir tree (only they fold into
                        # directory summaries).
                        okind = plugin.object_kind_of(ch.uri)
                        if self._pipeline.routes_to_pipeline(okind):
                            self._pipeline.job_lane.on_yield_object_change(job_id, ch.uri, okind)
            finally:
                stop_hb.set()
                hb.cancel()
                try:
                    await hb
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            # Finalize the dir tree (done for both inline and enqueue models).
            self._pipeline.job_lane.on_sync_done(job_id)
            if not process:
                # Stash staged state; the worker commits it only on success so a
                # failed job doesn't advance the cursor past un-indexed objects.
                await self._obj.set_job_state_snapshot(job_id, json.dumps(ctx.state.snapshot()))
                # Expose to workers only now: it was 'preparing' (unclaimable) during
                # enumeration, avoiding a worker finalizing it 'succeeded' pre-tasks.
                await self._obj.queue_preparing_job(job_id)
                return job_id
            aborted = await self.run_job(job_id, cid, connector_uri, plugin)
        except Exception as e:  # noqa: BLE001
            # Drop half-enqueued tasks and finalize 'failed' to free the slot.
            await self._obj.cancel_pending_tasks_for_job(job_id)
            await self.finalize_job(job_id, f"sync_error: {e}")
            raise
        finally:
            if plugin is not None:
                try:
                    await plugin.close()
                except Exception:  # noqa: BLE001
                    pass
        status = await self.finalize_job(job_id, aborted)
        # Commit the cursor only on a clean run; partial jobs get reconsidered next sync.
        if aborted is None and status == "succeeded":
            await ctx.state.commit()
        return job_id

    async def finalize_job(self, job_id: str, aborted: str | None) -> str:
        """Set terminal job status + per-status object counts, then evict the Job
        Lane dir tree. Returns the terminal status."""
        status = await self._obj.finalize_job(job_id, aborted)
        # Free the Job Lane's in-memory dir tree.
        if self._pipeline.job_lane is not None:
            self._pipeline.job_lane.evict_job(job_id)
        return status

    async def cancel_job(self, job_id: str) -> bool:
        """Cancel a job + its pending/running tasks. A running worker stops at the
        next per-object boundary (between objects, not within one); the embed
        consumer's NEXT flush skips this job's chunks. Returns False if already
        terminal or if this caller lost the cancel race."""
        status = await self._obj.get_job_status(job_id)
        if not status or status in ("succeeded", "partial", "failed", "cancelled"):
            return False
        won = await self._obj.cancel_job_row(job_id)
        if not won:
            return False
        await self._obj.cancel_pending_running_tasks_for_job(job_id)
        if self._pipeline.embed_consumer is not None:
            self._pipeline.embed_consumer.mark_job_cancelled(job_id)
        return True

    # --- map-phase execution: claim / retry / circuit breaker / heartbeat ---

    async def claim_batch(self, limit: int, connector_id: str) -> list[dict]:
        """Claim up to `limit` pending tasks for one connector, ordered by priority
        then age. Scoped per-connector because each task runs with this connector's
        plugin. The `change_kind != 'dir_summary'` guard excludes stray rows
        (dir_summary is Job-Lane-owned, never enqueued as a task)."""
        return await self._obj.claim_tasks(connector_id, limit)

    @staticmethod
    def classify_error(e: Exception) -> str:
        """Classify an embedding/provider error as 'auth' (bad key, non-retryable),
        'quota' (exhausted, non-retryable), or 'retryable' (transient). 'auth'/'quota'
        are global: the caller aborts the whole job rather than grinding every object."""
        m = str(e).lower()
        nm = type(e).__name__.lower()
        auth_markers = (
            "invalid_api_key",
            "invalid x-api-key",
            "authentication",
            "unauthorized",
            "permission denied",
            "401",
        )
        if any(k in m for k in auth_markers) or "authentication" in nm or "permissiondenied" in nm:
            return "auth"
        # Quota (insufficient_quota / 402) is non-retryable, unlike a transient 429.
        if "insufficient_quota" in m or "402" in m:
            return "quota"
        return "retryable"

    async def process_with_retry(self, plugin, connector_uri: str, task: dict) -> str | None:
        """Returns None on success, 'retryable_exhausted', 'skipped', or an
        embedding_* code. 'skipped' = a local per-object event (source vanished /
        type changed): recorded as status='skipped', breaker untouched."""
        import asyncio as _a

        max_r = self._cfg.object_task.max_retries
        for attempt in range(max_r + 1):
            try:
                # None = done (caller marks succeeded); 'deferred' = pipeline okind
                # completed async by the EmbedConsumer hook.
                return await self._indexer.handle(plugin, connector_uri, task)
            except _PER_OBJECT_SKIP_ERRORS as e:
                # Source vanished / type changed: don't retry, don't count toward the
                # breaker. Recorded as 'skipped' (visible without inflating failed_objects).
                await self._obj.mark_task_skipped(
                    task["id"], f"{type(e).__name__}: source disappeared mid-sync"
                )
                uri = f"{connector_uri}{task.get('object_uri', '')}"
                logger.info("object %s skipped (source disappeared mid-sync)", uri)
                return "skipped"
            except Exception as e:  # noqa: BLE001
                kind = self.classify_error(e)
                if kind in ("auth", "quota"):
                    # Global non-retryable failure: abort the whole job on first occurrence.
                    code = "embedding_auth_failed" if kind == "auth" else "embedding_quota_exceeded"
                    await self._obj.mark_task_failed(task["id"], f"{code}: {e}")
                    self.warn_object_failed(connector_uri, task, e)
                    return code
                if str(e).startswith("field_missing"):
                    # Deterministic [[objects]] config error: don't retry, fail fast.
                    await self._obj.mark_task_failed(task["id"], str(e))
                    self.warn_object_failed(connector_uri, task, e)
                    return "retryable_exhausted"
                if attempt < max_r:
                    # Reset per-task embed state before retry: the failed producer may
                    # have pumped partial chunks that the re-pump must replace.
                    if self._pipeline.embed_consumer is not None:
                        self._pipeline.embed_consumer.on_task_retry(task["id"])
                    # Exponential backoff capped at backoff_max_ms.
                    delay_ms = min(
                        self._cfg.object_task.backoff_initial_ms * (2**attempt),
                        self._cfg.object_task.backoff_max_ms,
                    )
                    await _a.sleep(delay_ms / 1000)
                    continue
                await self._obj.mark_task_failed(task["id"], str(e))
                self.warn_object_failed(connector_uri, task, e)
                return "retryable_exhausted"
        return "retryable_exhausted"

    @staticmethod
    def warn_object_failed(connector_uri: str, task: dict, e: Exception) -> None:
        """Log a WARNING naming the failed object + reason; object_tasks rows (and
        their last_error) are pruned after the job, so the aggregate count alone
        wouldn't say which object failed."""
        uri = f"{connector_uri}{task.get('object_uri', '')}"
        reason = f"{type(e).__name__}: {e}".replace("\n", " ").strip()
        if len(reason) > 300:
            reason = reason[:297] + "..."
        logger.warning("object %s failed: %s", uri, reason)

    async def should_stop(self, job_id: str, cid: str) -> bool:
        """Stop the job if cancelled or its connector is being removed, so no Milvus
        writes happen after teardown begins."""
        if await self._obj.get_job_status(job_id) == "cancelled":
            return True
        return await self._obj.get_connector_status(cid) == "removing"

    async def heartbeat_loop(self, job_id: str, stop: asyncio.Event) -> None:
        """Keep the job's heartbeat fresh on a fixed cadence while the worker holds
        it. Tying the heartbeat to this loop's liveness makes a stale heartbeat mean
        'worker process dead' (not 'one slow object')."""
        while not stop.is_set():
            await self._obj.refresh_heartbeat(job_id)
            try:
                await asyncio.wait_for(stop.wait(), timeout=_HEARTBEAT_INTERVAL_S)
            except asyncio.TimeoutError:
                pass

    async def run_job(self, job_id: str, cid: str, connector_uri: str, plugin) -> str | None:
        """Returns None on normal completion, or a circuit-breaker reason string."""
        threshold = self._cfg.object_task.consecutive_fatal_threshold
        consec_fail = 0  # consecutive object failures (fatal OR retries exhausted)
        stop_hb = asyncio.Event()
        hb_task = asyncio.create_task(self.heartbeat_loop(job_id, stop_hb))
        try:
            r = await self.run_job_loop(job_id, cid, connector_uri, plugin, threshold, consec_fail)
            if r is not None:
                return r  # map phase aborted (cancel / circuit breaker)
            # Wait for the EmbedConsumer to write all pumped chunks + flip task
            # status before finalizing.
            await self.await_map_drained(job_id)
            if self._infra.summary.enabled and self._pipeline.job_lane is not None:
                # Wait for all directory summaries to be computed + persisted before
                # marking the job done.
                await self._pipeline.job_lane.await_done(job_id)
            return None
        finally:
            stop_hb.set()
            hb_task.cancel()
            try:
                await hb_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def await_map_drained(self, job_id: str) -> None:
        """Block until this job has no running map tasks (all pumped chunks written +
        status flipped by the success hook)."""
        while True:
            if await self._obj.count_running_tasks(job_id) == 0:
                return
            await asyncio.sleep(0.05)

    async def run_job_loop(
        self, job_id: str, cid: str, connector_uri: str, plugin, threshold: int, consec_fail: int
    ) -> str | None:
        # Claim this connector's pending object_tasks (dir_summary is Job-Lane-owned,
        # not a task).
        while True:
            if await self.should_stop(job_id, cid):
                return "cancelled"
            tasks = await self.claim_batch(64, cid)
            if not tasks:
                break
            for t in tasks:
                # Re-check the stop boundary before each task: a cancel/remove can
                # land mid-batch.
                if await self.should_stop(job_id, cid):
                    return "cancelled"
                r = await self.process_with_retry(plugin, connector_uri, t)
                if r is None:
                    # Inline okind done; only flip a task we still own (a task
                    # cancelled out from under us isn't revived to succeeded).
                    await self._obj.advance_task(
                        t["id"], TaskStatus.SUCCEEDED, from_status=TaskStatus.RUNNING
                    )
                    consec_fail = 0
                elif r == "deferred":
                    # Pipeline okind: the success hook flips status when chunks land.
                    consec_fail = 0
                elif r == "skipped":
                    # Local skip event still makes progress; reset the breaker so a
                    # burst of `rm`s doesn't accumulate into a false trip.
                    consec_fail = 0
                elif r in ("embedding_auth_failed", "embedding_quota_exceeded"):
                    # Global failure: abort the job (every object would fail identically).
                    await self._obj.cancel_pending_running_tasks_for_job(job_id)
                    return r
                else:
                    # Exhausted retries count toward the breaker: without it, a
                    # persistently rate-limited provider would grind the whole connector.
                    consec_fail += 1
                    if consec_fail >= threshold:
                        await self._obj.cancel_pending_running_tasks_for_job(job_id)
                        return "circuit_breaker_tripped"
        return None
