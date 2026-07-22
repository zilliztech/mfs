"""Engine: thin facade wiring the collaborators behind `mfs add` (register connector
-> job -> sync -> object_tasks -> process) and the read/upload/worker paths.

All behavior lives in the components assembled in __init__ (IngestOrchestrator,
ReadService, UploadService, WorkerScheduler, ConnectorManager, plus the storage and
pipeline singletons); methods here forward to them. Jobs run inline (process=True)
or are drained by the standalone worker (run_worker_*).
"""

from __future__ import annotations

import logging

from ..config import ServerConfig
from .components import ConnectorFactory, CredentialService
from .components.artifact_cache import ArtifactCacheService
from .components.object_repository import ObjectRepository
from .components.reads import ReadService
from .components.upload import UploadService
from .infra import InfraStack
from .ingest import IngestOrchestrator
from .manage import ConnectorManager
from .pipeline_supervisor import PipelineSupervisor
from .worker import WorkerScheduler

logger = logging.getLogger(__name__)


class Engine:
    def __init__(self, cfg: ServerConfig):
        self.cfg = cfg
        self.ns = cfg.namespace
        # 8 infra clients (meta/milvus/artifact_cache/tx_cache/embed/converter/vlm/summary).
        self.infra = InfraStack(cfg)
        # SQL for the four tables + the advance_task guard.
        self.objects = ObjectRepository(self.infra.meta, cfg)
        # Target resolution / credential redact+resolve / plugin build.
        self.connector_factory = ConnectorFactory(cfg, self.infra.meta)
        # artifact_cache table SQL + LRU + freshness; the bytes store lives on InfraStack.
        self.artifacts = ArtifactCacheService(
            cfg, self.infra.meta, self.infra.artifact_cache, self.objects
        )
        # Process singletons (EmbedConsumer / ProducerContext / JobLane / Watcher) + the
        # per-object finalize hook; reach via self.pipeline.*.
        self.pipeline = PipelineSupervisor(
            cfg, self.infra, self.artifacts, self.objects, self.connector_factory
        )
        # End-to-end sync-job execution (register -> drain -> process -> finalize -> cancel).
        self.ingest = IngestOrchestrator(
            cfg, self.infra, self.connector_factory, self.pipeline, self.objects, self.artifacts
        )
        # Read path (search/ls/cat/head/tail/grep/export/resolve_connector_uri).
        self.reads = ReadService(
            cfg, self.infra, self.connector_factory, self.objects, self.artifacts
        )
        # Tar + manifest-diff upload protocol; reuses the ingest entrypoints.
        self.upload = UploadService(cfg, self.infra, self.objects, self.ingest)
        # Queue claim + concurrent workers + reclaim; depends on ingest only.
        # Two-layer try/except is load-bearing (outer jid=None keeps a failed job
        # from killing the worker coroutine).
        self.worker = WorkerScheduler(
            cfg, self.infra, self.connector_factory, self.objects, self.ingest
        )
        # probe/estimate/inspect/remove; the add-failure rollback wires to remove.
        self.connector_manager = ConnectorManager(
            cfg, self.infra, self.connector_factory, self.objects, self.artifacts
        )
        self.ingest.bind_remover(self.connector_manager.remove_connector)

    async def startup(self, *, preload_local_models: bool = False) -> None:
        await self.infra.startup(preload_local_models=preload_local_models)
        await self.pipeline.startup()

    async def shutdown(self) -> None:
        await self.pipeline.shutdown()
        await self.infra.shutdown()

    async def _write_object_row(
        self, cid: str, relpath: str, st, indexable: bool, search_status: str, chunk_count: int
    ) -> None:
        """UPSERT the `objects` registry row (type/media/size/fingerprint + search_status +
        chunk_count)."""
        await self.objects.write_object_row(cid, relpath, st, indexable, search_status, chunk_count)

    # --- target resolution (file-only path) ---

    def _is_secret_key(cls, key: str) -> bool:
        return CredentialService.is_secret_key(key)

    @classmethod
    def _redact_config(cls, value, key_is_secret: bool = False):
        return CredentialService.redact(value, key_is_secret)

    @staticmethod
    def _resolve_ref(v):
        return CredentialService.resolve(v)

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
        """Register + sync + enqueue tasks. process=True (AIO default): run the job
        inline and return when done. process=False: leave the job 'queued' for a
        standalone worker to pick up via run_worker_*(). On an already-registered
        connector, --config is ignored unless update_config (change config via
        `mfs connector update`, not a re-sync)."""
        return await self.ingest.add(
            target, config, full=full, since=since, process=process, update_config=update_config
        )

    async def ingest_upload(self, *a, **kw):
        return await self.upload.ingest_upload(*a, **kw)

    # --- manifest-diff upload protocol: stable identity
    #     file://<client_id><abs-root>, byte-diff + index-diff both on the file_state table ---

    async def files_manifest(self, *a, **kw):
        return await self.upload.files_manifest(*a, **kw)

    async def files_upload(self, *a, **kw):
        return await self.upload.files_upload(*a, **kw)

    # --- standalone worker: poll DB queue, process queued jobs ---
    async def cancel_job(self, job_id: str) -> bool:
        """Cancel a job: mark it + its pending/running tasks cancelled. A running
        worker stops at the next per-object boundary (checked in ingest.run_job)."""
        return await self.ingest.cancel_job(job_id)

    async def run_worker_forever(self, *a, **kw):
        return await self.worker.run_forever(*a, **kw)

    # --- artifact cache: bytes on the local filesystem + a metadata row
    #     in artifact_cache, with LRU size eviction ---
    async def _put_artifact(
        self, ns: str, object_uri: str, kind: str, data: bytes, currency: str = ""
    ) -> str:
        return await self.artifacts.put_artifact(ns, object_uri, kind, data, currency)

    async def _drop_artifacts(self, ns: str, object_uri: str) -> None:
        await self.artifacts.drop_artifacts(ns, object_uri)

    async def _read_artifact(self, ns: str, object_uri: str, kind: str) -> bytes | None:
        return await self.artifacts.read_artifact(ns, object_uri, kind)

    async def _converted_md_stale(self, cid: str, object_uri: str, live_fp: str | None) -> bool:
        return await self.artifacts.converted_md_stale(cid, object_uri, live_fp)

    async def _read_artifact_fresh(
        self, ns: str, object_uri: str, kind: str, currency: str
    ) -> bytes | None:
        return await self.artifacts.read_artifact_fresh(ns, object_uri, kind, currency)

    async def _evict_artifacts_if_needed(self, ns: str) -> int:
        return await self.artifacts.evict_if_needed(ns)

    # --- search / resolve (ReadService) ---

    async def search(self, *a, **kw):
        return await self.reads.search(*a, **kw)

    async def resolve_connector_uri(self, *a, **kw):
        return await self.reads.resolve_connector_uri(*a, **kw)

    # --- connector management: probe / inspect / remove ---
    async def probe(self, *a, **kw):
        return await self.connector_manager.probe(*a, **kw)

    async def estimate(self, *a, **kw):
        return await self.connector_manager.estimate(*a, **kw)

    async def inspect(self, *a, **kw):
        return await self.connector_manager.inspect(*a, **kw)

    async def remove_connector(self, *a, **kw):
        return await self.connector_manager.remove_connector(*a, **kw)

    # --- read commands: ls / cat / head / tail / grep / export (ReadService) ---

    async def ls(self, *a, **kw):
        return await self.reads.ls(*a, **kw)

    async def cat(self, *a, **kw):
        return await self.reads.cat(*a, **kw)

    async def export(self, *a, **kw):
        return await self.reads.export(*a, **kw)

    async def head(self, *a, **kw):
        return await self.reads.head(*a, **kw)

    async def tail(self, *a, **kw):
        return await self.reads.tail(*a, **kw)

    async def grep(self, *a, **kw):
        return await self.reads.grep(*a, **kw)
