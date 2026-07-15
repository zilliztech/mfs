"""Engine: orchestration for `mfs add` (register connector -> job -> sync ->
object_tasks -> process). `_index_object` does the real per-object work: read ->
chunk/convert/VLM/summary -> embed -> Milvus upsert, per object_kind. Jobs run inline
(process=True) or are drained by the standalone worker (run_worker_*).

per-object atomic writes + job inheritance + circuit breaker.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

from ..common.converter import CONVERT_EXTS
from ..common.retrieval import build_filter, collapse_results, to_envelope
from ..config import ServerConfig
from ..connectors.base import SyncOptions
from ..storage.file_state import FileStateStore
from ..storage.milvus import MILVUS_MAX_RESULT_WINDOW
from .components import ConnectorFactory, ConnectorLocator, CredentialService
from .components.artifact_cache import ArtifactCacheService
from .components.object_repository import ObjectRepository
from .infra import InfraStack
from .ingest import IngestOrchestrator
from .pipeline_supervisor import PipelineSupervisor
from .producers.render import render_record, resolve_path
from .state import ConnectorStateStore

logger = logging.getLogger(__name__)

_HEAD_CACHE_N = 100  # rows pre-cached per structured object to speed `head`
_BARE_CAT_MAX_BYTES = 5 * 1024 * 1024  # bare `cat` (no range) rejects objects larger than this
_GREP_LINEAR_SCAN_MAX = 200  # cap on not-indexed files a single grep scans linearly
_JOB_STALE_AFTER_S = 120  # no heartbeat for this long => worker presumed dead
_WORKER_CONNECT_TIMEOUT_S = 30  # bound plugin.connect() in the worker so a hanging/unreachable
# connector fails its job cleanly instead of blocking the single in-process worker forever


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_rel(p: str) -> str:
    """Connector-relative path with a single leading '/' (file_state / object_uri convention)."""
    return "/" + p.lstrip("/")


def _validate_upload_member(m) -> None:
    """Reject archive members that tarfile could materialize outside the staging tree."""
    import posixpath as _posixpath

    if m.issym() or m.islnk():
        raise ValueError(f"links not allowed in upload: {m.name}")
    if not (m.isfile() or m.isdir()):
        raise ValueError(f"unsupported member in upload: {m.name}")
    rel = str(m.name or "")
    if not rel or _posixpath.isabs(rel) or any(part == ".." for part in rel.split("/")):
        raise ValueError(f"unsafe path in archive: {rel}")
    if _posixpath.normpath(rel) in ("", ".") and not m.isdir():
        raise ValueError(f"unsafe path in archive: {rel}")


_CODE_SYMBOL = re.compile(r"^\s*(def |class |func |fn |public |private |func\(|type )")


def _density_view(text: str, ext: str, density: str) -> str:
    """Skeleton view of a document/code object:
    peek = headings (markdown #) or code symbol lines only;
    skim = peek + the first non-blank line of prose under each heading.
    """
    lines = text.splitlines()
    is_md = ext in (".md", ".markdown", ".rst", ".txt", "")
    out: list[str] = []
    if is_md:
        for i, ln in enumerate(lines):
            if ln.lstrip().startswith("#"):
                out.append(ln.rstrip())
                if density == "skim":
                    for nxt in lines[i + 1 :]:
                        if nxt.strip():
                            out.append("    " + nxt.strip()[:120])
                            break
    else:
        for ln in lines:
            if _CODE_SYMBOL.match(ln):
                out.append(ln.rstrip() if density == "skim" else ln.split("(")[0].rstrip())
    if not out:
        # nothing structural found -> first lines as a fallback peek
        out = [ln.rstrip() for ln in lines[:15]]
    return "\n".join(out)


class Engine:
    def __init__(self, cfg: ServerConfig):
        self.cfg = cfg
        self.ns = cfg.namespace
        # Infra stack: constructs + lifecycles the 8 infra clients (meta/milvus/artifact_cache/tx_cache/embed/converter/vlm/summary).
        self.infra = InfraStack(cfg)
        # ObjectRepository owns all SQL for the four tables + the advance_task guard.
        # Inject the shared meta handle + cfg (namespace derived from cfg).
        self.objects = ObjectRepository(self.infra.meta, cfg)
        # ConnectorFactory owns target resolution / credential redact+resolve / plugin build.
        self.connector_factory = ConnectorFactory(cfg, self.infra.meta)
        # ArtifactCacheService owns artifact_cache table SQL + LRU + freshness; the bytes store (LocalArtifactCache) lives on InfraStack (self.infra.artifact_cache).
        self.artifacts = ArtifactCacheService(
            cfg, self.infra.meta, self.infra.artifact_cache, self.objects
        )
        # Pipeline process singletons (EmbedConsumer / ProducerContext / JobLane / Watcher / _pending_finalize) - assembled + lifecycle by PipelineSupervisor;
        # reach via self.pipeline.*. _pending_finalize is owned solely by the supervisor (stash_finalize).
        self.pipeline = PipelineSupervisor(
            cfg, self.infra, self.artifacts, self.objects, self.connector_factory
        )
        # IngestOrchestrator owns end-to-end sync-job execution (register -> drain ->
        # map-phase process -> index -> finalize -> cancel). bind_remover wires the
        # add-failure rollback to Engine.remove_connector (stage-5 ConnectorManager
        # will swap in its own .remove).
        self.ingest = IngestOrchestrator(
            cfg, self.infra, self.connector_factory, self.pipeline, self.objects, self.artifacts
        )
        self.ingest.bind_remover(self.remove_connector)

    async def startup(self, *, preload_local_models: bool = False) -> None:
        # Infra connect + schema + optional model preload, then the pipeline process singletons
        # + startup reconcile (orphan GC + Job Lane recovery) + ConnectorJobWatcher.
        await self.infra.startup(preload_local_models=preload_local_models)
        await self.pipeline.startup()

    async def shutdown(self) -> None:
        # Pipeline (watcher + Job Lane + EmbedConsumer), then infra (meta + tx_cache).
        await self.pipeline.shutdown()
        await self.infra.shutdown()

    async def _write_object_row(
        self, cid: str, relpath: str, st, indexable: bool, search_status: str, chunk_count: int
    ) -> None:
        """UPSERT the `objects` registry row (type/media/size/fingerprint + search_status +
        chunk_count). Thin delegate to ObjectRepository; shared by the inline _index_object
        tail and the pipeline success hook."""
        await self.objects.write_object_row(cid, relpath, st, indexable, search_status, chunk_count)

    # --- target resolution (file-only path) ---
    def _resolve_target(self, target: str) -> tuple[str, str, str, dict]:
        # Thin delegate to ConnectorFactory (dispatches to the plugin class's
        # derive_target). Kept on Engine to preserve the original signature /
        # call sites.
        r = self.connector_factory.resolve_target(target)
        return r.ctype, r.connector_uri, r.scheme, r.config

    @classmethod
    def _is_secret_key(cls, key: str) -> bool:
        # Thin delegate to ConnectorFactory (CredentialService).
        return CredentialService.is_secret_key(key)

    @classmethod
    def _redact_config(cls, value, key_is_secret: bool = False):
        # Thin delegate to ConnectorFactory (CredentialService). Redaction logic now
        # lives in the single security entry point; this keeps the old call sites.
        return CredentialService.redact(value, key_is_secret)

    @staticmethod
    def _resolve_ref(v):
        # Thin delegate to ConnectorFactory (CredentialService.resolve). Kept as a
        # staticmethod for the original call sites; the single security entry point
        # does the actual env:/file:/secret:/vault: handling.
        return CredentialService.resolve(v)

    def _build_plugin(self, ctype: str, config: dict, connector_id: str):
        # Thin delegate to ConnectorFactory (PluginBuilder). Returns the original
        # (plugin, ctx) tuple so all call sites stay unchanged.
        built = self.connector_factory.build_plugin(ctype, config, connector_id)
        return built.plugin, built.ctx

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

    async def ingest_upload(
        self, name: str, data: bytes, fmt: str = "tar", process: bool = True
    ) -> dict:
        """CS upload flow: client/server don't share a fs, so the client
        ships a tar(.gz) of the tree (?name=<label>). The label is the connector's stable
        identity file://<name> — the SAME file://<client_id><root> shape the manifest-diff
        flow uses — so the upload is searchable / removable by that logical URI rather than
        by the server's internal staging path (which the old code leaked as file://local…,
        diverging from the manifest flow). Full-tree snapshot; guards zip-slip."""
        import hashlib
        import io
        import tarfile

        # Validate the body IS a readable, non-empty tar BEFORE registering a connector, so a
        # garbage / empty bundle returns a clean 400 and leaves no residual connector behind
        # (a non-tar throws tarfile.ReadError; an all-zero body parses as an empty archive).
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as _probe:
                members = _probe.getmembers()
                if not members:
                    raise ValueError("invalid or empty upload bundle")
                for m in members:
                    _validate_upload_member(m)
        except tarfile.TarError as e:
            raise ValueError("invalid or empty upload bundle") from e

        staging, connector_uri, cid = await self._staging_connector(name, "")
        fs = FileStateStore(self.infra.meta, self.ns, cid)

        def _safe(rel: str) -> str:
            dest = os.path.realpath(os.path.join(staging, rel))
            if dest != staging and not dest.startswith(staging + os.sep):
                raise ValueError(f"unsafe path in archive: {rel}")
            return dest

        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
            members = tf.getmembers()
            for m in members:  # validate EVERY member before any side effect
                _validate_upload_member(m)
                _safe(m.name)  # incl. directory entries: a lone `../escaped`
                #                               dir member would otherwise extractall outside
                #                               staging (zip-slip via a directory, not a file)
            # reserve the sync slot BEFORE mutating staging/file_state (so a rejected sync —
            # sync_already_running — leaves nothing half-applied), then stage the tree.
            job_id = await self.ingest._open_sync_job(cid, process)
            tf.extractall(staging)  # validated above
            for m in members:
                if m.isdir():
                    continue
                real = _safe(m.name)
                st = os.stat(real)
                sha1 = hashlib.sha1(open(real, "rb").read()).hexdigest()
                await fs.upsert(
                    _norm_rel(m.name), st.st_size, st.st_mtime_ns, st.st_ino, sha1, status="staged"
                )
        crow = await self.objects.get_connector_config(cid)
        stored_cfg = json.loads(crow["config_json"]) if crow and crow["config_json"] else {}
        await self.ingest._drain_job(
            job_id, cid, connector_uri, "file", stored_cfg, False, None, process
        )
        return {"job_id": job_id, "connector_uri": connector_uri, "staging": staging}

    # --- manifest-diff upload protocol: stable identity
    #     file://<client_id><abs-root>, byte-diff + index-diff both on the file_state table ---
    def _staging_root(self, client_id: str, root: str) -> str:
        import hashlib

        sub = hashlib.sha1(f"{client_id}:{root}".encode()).hexdigest()[:16]
        return os.path.realpath(str(self.infra.artifact_cache.files_root(self.ns, sub)))

    async def _staging_connector(self, client_id: str, root: str):
        """(staging_dir, connector_uri, connector_id). The connector's stable identity is
        file://<client_id><client-abs-root> so the user can later search / remove by the
        original local path; the bytes physically live in a server-side staging dir."""
        staging = self._staging_root(client_id, root)
        connector_uri = f"file://{client_id}{root}"
        cid = await self.ingest.register_or_get_connector(
            connector_uri, "file", {"root": staging, "client_id": client_id, "upload_mode": True}
        )
        return staging, connector_uri, cid

    async def files_manifest(self, client_id: str, root: str, files: list[dict]) -> dict:
        """Step ②: diff the client's stat-only manifest against the
        server-side file_state (the same table the file connector uses) and return which
        paths' bytes are needed + deletion candidates (with sha1/inode for rename pairing)."""
        staging, connector_uri, cid = await self._staging_connector(client_id, root)
        fs = FileStateStore(self.infra.meta, self.ns, cid)
        # file_state stores connector-relative paths with a leading '/' (same convention as
        # the file connector, so object_uri = connector_uri + path joins cleanly); the client
        # speaks slash-less relpaths, so normalize on the boundary.
        prev = {r["path"]: r for r in await fs.all_rows()}  # keys '/auth.md'
        client = {f["path"]: f for f in files}  # keys 'auth.md'
        need_sha1 = [
            p
            for p, f in client.items()
            if _norm_rel(p) not in prev
            or prev[_norm_rel(p)]["size"] != f.get("size")
            or prev[_norm_rel(p)]["mtime_ns"] != f.get("mtime_ns")
        ]
        deletion_candidates = [
            {"path": p.lstrip("/"), "size": r["size"], "inode": r["inode"], "sha1": r["sha1"]}
            for p, r in prev.items()
            if p.lstrip("/") not in client
        ]
        return {
            "connector_uri": connector_uri,
            "staging": staging,
            "need_sha1": need_sha1,
            "deletion_candidates": deletion_candidates,
        }

    async def files_upload(
        self, client_id: str, root: str, bundle: bytes, process: bool = True, full: bool = False
    ) -> dict:
        """Step ④: validate the bundle in a temp dir (sha1), then in one
        commit apply renames / changed bytes / deletions to the staging area and UPSERT
        file_state (status='staged'); the file connector then indexes the staged rows.
        The bundle is a tar(.gz) carrying a `.mfs-meta.json` {hashes,renames,deletions}
        member plus the changed file bytes. zip-slip + sha1 guarded."""
        import hashlib
        import io
        import json as _json
        import shutil
        import tarfile
        import tempfile

        # Validate the bundle IS a readable, non-empty tar BEFORE registering a connector, so a
        # garbage / empty bundle returns a clean 400 and leaves no residual connector behind
        # (a non-tar throws tarfile.ReadError; an all-zero body parses as an empty archive).
        try:
            with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:*") as _probe:
                members = _probe.getmembers()
                if not members:
                    raise ValueError("invalid or empty upload bundle")
                for m in members:
                    if m.name == ".mfs-meta.json":
                        if not m.isfile():
                            raise ValueError("invalid upload metadata")
                        continue
                    _validate_upload_member(m)
                mm = next((m for m in members if m.name == ".mfs-meta.json"), None)
                if mm:
                    _json.loads(_probe.extractfile(mm).read().decode())
        except tarfile.TarError as e:
            raise ValueError("invalid or empty upload bundle") from e

        staging, connector_uri, cid = await self._staging_connector(client_id, root)
        fs = FileStateStore(self.infra.meta, self.ns, cid)

        def _safe(base: str, rel: str) -> str:
            dest = os.path.realpath(os.path.join(base, rel))
            if dest != base and not dest.startswith(base + os.sep):
                raise ValueError(f"unsafe path in archive: {rel}")
            return dest

        tmp = tempfile.mkdtemp(prefix=".upload-", dir=os.path.dirname(staging))
        try:
            with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:*") as tf:
                members = tf.getmembers()
                for m in members:
                    if m.name != ".mfs-meta.json":
                        _validate_upload_member(m)
                        _safe(staging, m.name)
                        _safe(tmp, m.name)
                    elif not m.isfile():
                        raise ValueError("invalid upload metadata")
                mm = next((m for m in members if m.name == ".mfs-meta.json"), None)
                meta = _json.loads(tf.extractfile(mm).read().decode()) if mm else {}
                hashes = {h["path"]: h for h in meta.get("hashes", [])}
                renames = meta.get("renames", [])
                deletions = meta.get("deletions", [])
                for m in members:
                    if m.name == ".mfs-meta.json" or m.isdir():
                        continue
                    tf.extract(m, tmp)
                for m in members:  # verify each payload's sha1 before touching staging
                    if m.name == ".mfs-meta.json" or m.isdir():
                        continue
                    h = hashes.get(m.name) or hashes.get("/" + m.name)
                    if h and h.get("sha1"):
                        got = hashlib.sha1(open(_safe(tmp, m.name), "rb").read()).hexdigest()
                        if got != h["sha1"]:
                            raise ValueError(f"sha1 mismatch for {m.name}")

            # bundle fully validated in temp; NOW reserve the sync slot. If a sync is
            # already in flight this raises sync_already_running and the staging area +
            # file_state are still untouched.
            job_id = await self.ingest._open_sync_job(cid, process)

            # --- apply to staging + file_state (status='staged') ---
            for r in renames:  # 1) renames: verify server sha1, mv, carry file_state
                old, new = _norm_rel(r["old"]), _norm_rel(r["new"])
                prev = await fs.get(old)
                if not prev or prev["sha1"] != r.get("sha1"):
                    continue  # reject -> client re-sends bytes next round
                op, npth = _safe(staging, r["old"]), _safe(staging, r["new"])
                if os.path.exists(op):
                    os.makedirs(os.path.dirname(npth), exist_ok=True)
                    os.replace(op, npth)
                await fs.delete(old)
                await fs.upsert(
                    new,
                    prev["size"],
                    prev["mtime_ns"],
                    prev["inode"],
                    prev["sha1"],
                    status="staged",
                    renamed_from=old,
                )
            for h in hashes.values():  # 2) changed bytes -> staging + file_state staged
                src = _safe(tmp, h["path"])
                if os.path.exists(src):
                    dst = _safe(staging, h["path"])
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    os.replace(src, dst)
                    await fs.upsert(
                        _norm_rel(h["path"]),
                        h.get("size"),
                        h.get("mtime_ns"),
                        h.get("inode"),
                        h.get("sha1"),
                        status="staged",
                    )
            for d in deletions:  # 3) deletions: mark file_state 'deleted' so the sync
                dp = _safe(staging, d)  #    drops the index, then on_object_deleted drops the row
                if os.path.exists(dp):
                    os.remove(dp)
                prev = await fs.get(_norm_rel(d))
                if prev:
                    await fs.upsert(
                        _norm_rel(d),
                        prev["size"],
                        prev["mtime_ns"],
                        prev["inode"],
                        prev["sha1"],
                        status="deleted",
                    )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        crow = await self.objects.get_connector_config(cid)
        stored_cfg = _json.loads(crow["config_json"]) if crow and crow["config_json"] else {}
        # full=True (--force-index / --force-upload): upload-mode sync also re-yields the
        # already-indexed staging rows so a forced rebuild re-embeds the whole tree.
        await self.ingest._drain_job(
            job_id, cid, connector_uri, "file", stored_cfg, full, None, process
        )
        return {"job_id": job_id, "connector_uri": connector_uri, "staging": staging}

    # --- standalone worker: poll DB queue, process queued jobs ---
    async def cancel_job(self, job_id: str) -> bool:
        """Cancel a job: mark it + its pending/running tasks cancelled. A running
        worker stops at the next per-object boundary (checked in ingest._run_job)."""
        return await self.ingest.cancel_job(job_id)

    async def _claim_queued_job(self) -> dict | None:
        """Atomically claim the oldest queued job. Multi-worker safe: the claim is a
        conditional UPDATE guarded on status='queued', and we take the job only when
        *this* worker's UPDATE flipped the row (rowcount == 1). Two workers racing the
        same job -> only one's UPDATE matches; the loser tries the next candidate.
        Thin delegate to ObjectRepository.claim_queued_job."""
        return await self.objects.claim_queued_job()

    async def run_worker_once(self) -> str | None:
        """Claim + process one queued job. Returns its id, or None if queue empty."""
        import json

        job = await self._claim_queued_job()
        if not job:
            return None
        cid = job["connector_id"]
        crow = await self.objects.get_connector_root_type_config(cid)
        connector_uri, ctype = crow["root_uri"], crow["type"]
        stored_cfg = json.loads(crow["config_json"]) if crow["config_json"] else {}
        plugin = None
        try:
            plugin, _ = self._build_plugin(ctype, stored_cfg, cid)
            # Bound connect(): an unreachable/hanging connector (or one whose persisted creds
            # no longer resolve) must fail THIS job cleanly, not block the single in-process
            # sqlite worker forever — one bad connector cannot be allowed to wedge all ingest.
            await asyncio.wait_for(plugin.connect(), timeout=_WORKER_CONNECT_TIMEOUT_S)
            aborted = await self.ingest._run_job(job["id"], cid, connector_uri, plugin)
            await self.ingest._finalize_job(job["id"], aborted)
            # commit the deferred connector state only on a FULLY clean run: a
            # failed/cancelled/partial job leaves the cursor where it was, so a
            # partial job's failed objects (and the successful ones alongside
            # them) get reconsidered on the next sync rather than the cursor
            # skipping past them. Each connector's own fingerprint check keeps
            # that cheap -- the already-succeeded objects get skipped quickly,
            # only the failed ones actually redo real work.
            if aborted is None:
                jrow = await self.objects.get_job_state_and_status(job["id"])
                if jrow and jrow["status"] == "succeeded" and jrow["state_snapshot"]:
                    await ConnectorStateStore(self.infra.meta, cid).apply(
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
            await self.objects.fail_running_tasks_for_job(job["id"], str(reason))
            await self.objects.fail_inflight_job(job["id"], str(reason))
            logger.warning("sync job %s for %s failed: %s", job["id"], connector_uri, reason)
        finally:
            if plugin is not None:
                try:
                    await plugin.close()
                except Exception:  # noqa: BLE001
                    pass
        return job["id"]

    def _resolve_concurrency(self, concurrency=None) -> int:
        c = concurrency if concurrency is not None else self.cfg.chunks_producer.concurrency
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
        — a single un-recoverable job must not abort (and thus starve) the reclaim of every
        other orphan, which would wedge crash-recovery for all connectors."""
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=stale_after_s)).isoformat()

        # Fail stale 'preparing' jobs: one whose process died mid-enumeration never started
        # running, and while it lingers it holds the connector's one-active-job slot, blocking
        # any new sync from being enqueued for that connector at all.
        try:
            stale_prep = await self.objects.list_stale_preparing_jobs(cutoff)
        except Exception as e:  # noqa: BLE001
            logger.warning("reclaim: listing stale preparing jobs failed: %s", e)
            stale_prep = []
        for j in stale_prep:
            try:
                await self.objects.fail_stale_preparing_job(j["id"])
            except Exception as e:  # noqa: BLE001
                logger.warning("reclaim: failing stale preparing job %s: %s", j["id"], e)

        try:
            stale = await self.objects.list_stale_running_jobs(cutoff)
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
                await self.objects.reset_running_tasks_to_pending(j["id"])
                await self.objects.requeue_stale_running_job(j["id"])
            except Exception as e:  # noqa: BLE001 — one un-recoverable orphan must not starve the rest
                logger.warning("reclaim: recovering stale running job %s: %s", j["id"], e)

    async def run_worker_forever(self, poll_interval: float = 1.0, concurrency=None) -> None:
        """Drain the queued-job queue with `concurrency` parallel workers. Each worker
        atomically claims a distinct job (the conditional claim is race-free), so N
        connectors' sync jobs run in parallel. Idle workers run a housekeeping pass that
        reclaims jobs orphaned by a crashed worker (stale heartbeat)."""
        n = self._resolve_concurrency(concurrency)

        async def _loop() -> None:
            while True:
                try:
                    jid = await self.run_worker_once()
                except Exception:  # noqa: BLE001 — a single job must NEVER kill the worker
                    # coroutine; with the sqlite single worker that would wedge all ingest.
                    jid = None
                if jid is None:
                    await self._reclaim_stale_jobs()
                    await asyncio.sleep(poll_interval)

        await asyncio.gather(*[_loop() for _ in range(n)])

    async def _read_text(self, plugin, relpath: str) -> str:
        return (await self._read_bytes(plugin, relpath)).decode("utf-8", errors="replace")

    async def _read_bytes(self, plugin, relpath: str) -> bytes:
        buf = bytearray()
        async for chunk in plugin.read(relpath):
            buf += chunk
        return bytes(buf)

    # --- artifact cache: bytes on the local filesystem + a metadata row
    #     in artifact_cache, with LRU size eviction ---
    async def _put_artifact(
        self, ns: str, object_uri: str, kind: str, data: bytes, currency: str = ""
    ) -> str:
        # Thin delegate to ArtifactCacheService (metadata row + LRU throttle). Kept on
        # Engine to preserve the original signature / ArtifactStoreAdapter wiring.
        return await self.artifacts.put_artifact(ns, object_uri, kind, data, currency)

    async def _drop_artifacts(self, ns: str, object_uri: str) -> None:
        # Thin delegate to ArtifactCacheService.
        await self.artifacts.drop_artifacts(ns, object_uri)

    async def _read_artifact(self, ns: str, object_uri: str, kind: str) -> bytes | None:
        # Thin delegate to ArtifactCacheService.
        return await self.artifacts.read_artifact(ns, object_uri, kind)

    async def _converted_md_stale(self, cid: str, object_uri: str, live_fp: str | None) -> bool:
        # Thin delegate to ArtifactCacheService (reads ObjectRepository.fingerprint).
        return await self.artifacts.converted_md_stale(cid, object_uri, live_fp)

    async def _read_artifact_fresh(
        self, ns: str, object_uri: str, kind: str, currency: str
    ) -> bytes | None:
        # Thin delegate to ArtifactCacheService.
        return await self.artifacts.read_artifact_fresh(ns, object_uri, kind, currency)

    async def _evict_artifacts_if_needed(self, ns: str) -> int:
        # Thin delegate to ArtifactCacheService.
        return await self.artifacts.evict_if_needed(ns)

    # --- search ---
    async def _has_registered_search_scope(self, connector_uri: str | None) -> bool:
        """Return whether a search scope can match registered connector-owned chunks.

        Searching an empty namespace, or a path that resolves to an unregistered connector
        URI, cannot produce hits. Fast-pathing that case avoids cold-starting the query
        embedder for a guaranteed-empty result.
        """
        if connector_uri is None:
            return await self.objects.has_any_connector()
        return await self.objects.has_connector_uri(connector_uri)

    async def search(
        self,
        query: str,
        connector_uri: str | None = None,
        object_prefix: str | None = None,
        mode: str = "hybrid",
        top_k: int = 10,
        chunk_kinds: list[str] | None = None,
        collapse: bool = False,
    ) -> list[dict]:
        if top_k <= 0 or not query or not query.strip():
            return []  # nothing to ask for: skip the embed call and Milvus' limit>0 rule
        # Coarse fast-path: reject an absurd top_k before embedding/querying. Hybrid
        # over-fetches each sub-search by over_fetch_ratio, so the request Milvus sees is
        # top_k * ratio; other modes send top_k directly. This only catches values above the
        # hard window — the backend's real per-search cap is lower and backend-specific
        # (Milvus Lite tops out far below Zilliz), so MilvusStore translates the actual
        # MilvusException into the same `top_k_too_large` error as the authoritative guard.
        effective = top_k * self.cfg.search.over_fetch_ratio if mode == "hybrid" else top_k
        if effective > MILVUS_MAX_RESULT_WINDOW:
            raise ValueError("top_k_too_large")
        if not await self._has_registered_search_scope(connector_uri):
            return []
        expr = build_filter(self.ns, connector_uri, object_prefix, chunk_kinds)
        if mode == "keyword":
            hits = await asyncio.to_thread(
                self.infra.milvus.sparse_search, self.ns, query, top_k, expr
            )
        else:
            qvec = (await self.infra.embed.batch_embed([query]))[0]
            if mode == "semantic":
                hits = await asyncio.to_thread(
                    self.infra.milvus.search_dense, self.ns, qvec, top_k, expr
                )
            else:  # hybrid
                hits = await asyncio.to_thread(
                    self.infra.milvus.hybrid_search,
                    self.ns,
                    qvec,
                    query,
                    top_k,
                    expr,
                    None,
                    self.cfg.search.over_fetch_ratio,
                )
        envs = [to_envelope(h) for h in hits]
        return collapse_results(envs) if collapse else envs

    async def resolve_connector_uri(self, target: str) -> tuple[str, str | None]:
        """Map a user path/URI to (connector_uri, object_prefix) for search/grep scope.
        Matches the registered connector whose root is the longest prefix of `target`,
        so `search q /repo/src` (after `add /repo`) scopes to the /src subtree instead
        of fabricating a brand-new connector_uri that would match no indexed chunks."""
        match = await self._match_connector(target)
        if match is None:
            # not under any registered connector: fall back to literal resolution so an
            # exact connector root still scopes correctly (object_prefix unknown -> None).
            _, connector_uri, _, _ = self._resolve_target(target)
            return connector_uri, None
        row, rel = match
        connector_uri = row["root_uri"]
        # stored chunk object_uri == connector_uri + relpath, so prefix on the full URI
        object_prefix = (connector_uri + rel) if rel not in ("", "/") else None
        return connector_uri, object_prefix

    async def _resolve_readonly_config(
        self, ctype: str, connector_uri: str, config: dict | None, default_config: dict
    ) -> dict:
        """Config resolution shared by probe()/estimate(). When `--config` is
        omitted, reuse an already-registered connector's stored config (as
        inspect() does) instead of silently falling back to a URI-derived
        default — for schemes where the URI alone can't reconstruct real
        connection info (postgres/mysql/mongo/s3/web), that default is `{}`,
        which produces a connection to nothing meaningful (e.g. postgres
        falling through to libpq's OS-user ambient defaults) while still
        reporting a real-looking failure, misleading the caller into thinking
        their actual registered connector is broken.

        Also validates the resolved config against CONFIG_SCHEMA (if the
        connector declares one) before returning — probe/estimate are
        supposed to be a safe pre-flight check, so a bad config should be
        caught here too, not just at add/update time."""
        if config is not None:
            cfg_dict = {**default_config, **config}
        else:
            row = await self.objects.get_connector_id_and_config_by_uri(connector_uri)
            cfg_dict = (
                json.loads(row["config_json"]) if row and row["config_json"] else default_config
            )
        self.connector_factory.validate_config(ctype, cfg_dict)
        return cfg_dict

    # --- connector management: probe / inspect / remove ---
    async def probe(self, target: str, config: dict | None = None) -> dict:
        """Try-connect a connector without registering or writing state."""
        _, connector_uri, ctype, default_config = self._resolve_target(target)
        plugin = None
        try:
            # Resolve + validate config INSIDE the guard, same as the build/connect
            # below: a config validation error is a user config error just like a
            # missing/unresolvable env:/file: ref — it must come back as ok=false
            # like a failed connect/auth, not escape to the generic 500 handler.
            # NotImplementedError (an uninstalled connector extra) is intentionally
            # NOT caught here so it still renders as the 501 not_available envelope.
            cfg_dict = await self._resolve_readonly_config(
                ctype, connector_uri, config, default_config
            )
            plugin, _ = self._build_plugin(ctype, cfg_dict, "probe-" + uuid.uuid4().hex)
            await plugin.connect()
            hs = await plugin.healthcheck()
            return {"target": connector_uri, "type": ctype, "ok": hs.ok, "detail": hs.detail}
        except NotImplementedError:
            raise
        except Exception as e:  # noqa: BLE001
            return {"target": connector_uri, "type": ctype, "ok": False, "detail": str(e)}
        finally:
            if plugin is not None:
                try:
                    await plugin.close()
                except Exception:  # noqa: BLE001
                    pass

    async def estimate(
        self,
        target: str,
        config: dict | None = None,
        since: str | None = None,
        sample_objects: int = 3,
        sample_records: int = 1000,
    ) -> dict:
        """Zero-billing pre-flight estimate: enumerate the object set
        (metadata-only) and run the chunker + local tokenizer on a small sample to
        extrapolate physical work (chunks / tokens). Never calls the embedding API or
        writes Milvus — the user sees the prompt before any money is spent. Returns
        physical quantities only (no $/time, per design)."""
        from ..processors.text import chunk_body

        _, connector_uri, ctype, default_config = self._resolve_target(target)
        cfg_dict = await self._resolve_readonly_config(ctype, connector_uri, config, default_config)
        tmp_cid = "estimate-" + uuid.uuid4().hex
        plugin, _ = self._build_plugin(ctype, cfg_dict, tmp_cid)
        await plugin.connect()
        try:
            # Gate on the connector's own try-connect so a bad root (e.g. a single
            # file instead of a directory) surfaces the plugin's descriptive detail
            # rather than a cryptic walk failure mapped to connector_unhealthy.
            hs = await plugin.healthcheck()
            if not hs.ok:
                raise ValueError(hs.detail or "connector_unhealthy")
            obj_uris: list[str] = []
            # dry_run: enumerate object URIs without hashing bytes or writing any state
            # estimate must be side-effect-free and cheap.
            async for ch in plugin.sync(SyncOptions(full=True, dry_run=True, since=since)):
                if ch.kind != "deleted":
                    obj_uris.append(ch.uri)
                if len(obj_uris) >= 200000:
                    break
            total = len(obj_uris)
            try:
                import tiktoken

                enc = tiktoken.get_encoding("cl100k_base")
                ntok = lambda s: len(enc.encode(s))  # noqa: E731
            except Exception:  # noqa: BLE001 - tokenizer unavailable -> ~4 chars/token
                ntok = lambda s: max(1, len(s) // 4)  # noqa: E731
            s_chunks = s_tokens = s_objs = 0
            for rel in obj_uris[:sample_objects]:
                okind = plugin.object_kind_of(rel)
                texts: list[str] = []
                if okind in ("document", "code", "text_blob"):
                    ext = os.path.splitext(rel)[1].lower()
                    text = await self._read_text(plugin, rel)
                    texts = [
                        t for t, _ in chunk_body(text, okind, ext, self.cfg.chunking.chunk_size)
                    ]
                elif okind in ("table_rows", "record_collection", "message_stream"):
                    ocfg = plugin.ctx.object_config_for(rel)
                    records = plugin.read_records(rel)
                    sampled = 0  # records actually consumed (≤ sample_records)
                    if records is not None and ocfg.text_fields:
                        try:
                            async for rec in records:
                                t = render_record(rec, ocfg.text_fields, ocfg.render_template)
                                if t.strip():
                                    texts.append(t)
                                sampled += 1
                                if sampled >= sample_records:
                                    break
                        finally:
                            # Breaking out of `async for` does NOT close the async generator,
                            # so a connector that yields from inside `async with pool.acquire()`
                            # (mysql/postgres/mongo) keeps a DB connection pinned by the
                            # suspended generator; the estimate's `plugin.close()` then deadlocks
                            # in pool.wait_closed() waiting for that connection. Explicitly close
                            # the generator after the capped sample so the connection is released.
                            aclose = getattr(records, "aclose", None)
                            if aclose is not None:
                                await aclose()
                    # Ask the plugin for the real record count; if cheap and known,
                    # extrapolate per-record averages over the full count instead of
                    # summing the truncated sample. Otherwise fall back to summing
                    # what we sampled (matches the old behavior — a known
                    # under-count when one object contains many records).
                    rec_total: int | None = None
                    if okind in ("table_rows", "record_collection", "message_stream"):
                        try:
                            rec_total = await plugin.record_count(rel)
                        except Exception:  # noqa: BLE001 - estimate must never fail
                            rec_total = None
                    if rec_total is not None and rec_total > sampled > 0 and texts:
                        per_rec_chunks = len(texts) / sampled
                        per_rec_tokens = sum(ntok(t) for t in texts) / sampled
                        s_chunks += per_rec_chunks * rec_total
                        s_tokens += per_rec_tokens * rec_total
                        texts = []  # already accounted for, don't re-count below
                if texts:
                    s_chunks += len(texts)
                    s_tokens += sum(ntok(t) for t in texts)
                s_objs += 1
            per_chunks = (s_chunks / s_objs) if s_objs else 0
            per_tokens = (s_tokens / s_objs) if s_objs else 0
            return {
                "target": connector_uri,
                "type": ctype,
                "objects": total,
                "sampled_objects": s_objs,
                "est_chunks": int(per_chunks * total),
                "est_tokens": int(per_tokens * total),
            }
        finally:
            try:
                await plugin.close()
            except Exception:  # noqa: BLE001
                pass
            # belt-and-suspenders: drop any rows a connector's sync may have written under
            # the throwaway estimate id (dry_run covers file; this catches the rest so a
            # probe/estimate can never accrete orphan state nothing will ever clean up).
            for tbl in ("file_state", "connector_state"):
                try:
                    await self.infra.meta.execute(
                        f"DELETE FROM {tbl} WHERE connector_id=?", (tmp_cid,)
                    )
                except Exception:  # noqa: BLE001
                    pass

    async def inspect(self, target: str) -> dict | None:
        """Connector row + object/job summary."""
        match = await self._match_connector(target)
        if match is not None:
            matched, _ = match
            row = await self.objects.get_connector_row(matched["id"])
        else:
            _, connector_uri, _, _ = self._resolve_target(target)
            row = await self.objects.get_connector_row_by_uri(connector_uri)
        if not row:
            return None
        cid = row["id"]
        objs = await self.objects.summarize_objects_by_search_status(cid)
        jobs = await self.objects.summarize_jobs_by_status(cid)
        total = await self.objects.summarize_objects_totals(cid)
        return {
            **dict(row),
            "objects": {o["search_status"]: o["n"] for o in objs},
            "object_count": total["n"] or 0,
            "chunk_count": total["chunks"] or 0,
            "jobs": {j["status"]: j["n"] for j in jobs},
        }

    async def remove_connector(self, target: str) -> bool:
        """Remove a connector and everything it owns: Milvus chunks, artifacts, and all
        metadata rows (objects / tasks / jobs / state / file_state)."""
        _, connector_uri, _, _ = self._resolve_target(target)
        cid = await self.objects.get_connector_id_by_uri(connector_uri)
        if not cid:
            match = await self._match_connector(target)
            if match is None:
                raise ValueError("remove_requires_connector_root")
            matched, rel = match
            if rel != "/":
                raise ValueError("remove_requires_connector_root")
            cid = matched["id"]
            connector_uri = matched["root_uri"]
        # preempt any in-flight sync. Mark 'removing' (new syncs ->
        # connector_removing; a running worker observes it at its next task boundary via
        # _should_stop and exits). Cancel only the not-yet-started work (queued job +
        # pending tasks). Crucially DON'T flip the running job ourselves — its status
        # leaving 'running' is the signal that the worker has exited _run_job and no
        # _index_object is mid-write; only then is it safe to delete the data.
        await self.objects.set_connector_removing(cid)
        await self.objects.cancel_pending_tasks_for_connector(cid)
        await self.objects.cancel_queued_preparing_jobs(cid)
        # Wait for the worker to leave 'running' — that transition (set in _finalize_job
        # after _run_job's loop exits) is the proof the last _index_object's Milvus upsert
        # has completed, so it's the only safe moment to delete. Don't bound this by wall
        # clock (the old ~10s cap would delete out from under an object still mid-write,
        # re-opening the orphan-chunk race); instead trust the heartbeat. A live worker
        # refreshes it per task, so we keep waiting; only a stale heartbeat means the
        # worker died/stuck, in which case WE take the job over and then delete.
        stale_after_s = _JOB_STALE_AFTER_S
        while True:
            running = await self.objects.get_running_job_heartbeat(cid)
            if not running:
                break
            cutoff = (datetime.now(timezone.utc) - timedelta(seconds=stale_after_s)).isoformat()
            if not running["heartbeat"] or running["heartbeat"] < cutoff:
                # worker is dead or wedged — reclaim: cancel its in-flight tasks + the job
                # so the 'running' row clears and no later write can resurrect it.
                await self.objects.cancel_pending_running_tasks_for_job(running["id"])
                await self.objects.cancel_running_job(running["id"])
                break
            await asyncio.sleep(0.1)
        # 1. Milvus chunks for this connector partition (worker has now stopped writing)
        await asyncio.to_thread(self.infra.milvus.delete_by_connector, self.ns, connector_uri)
        # 2. best-effort artifact bytes per object
        objs = await self.objects.list_object_uris_for_connector(cid)
        for o in objs:
            await self._drop_artifacts(self.ns, connector_uri + o["object_uri"])
        # 3. metadata rows — the three target tables via the repo; connector_state / file_state
        # are out of this repo's four-table scope and stay here.
        await self.objects.delete_object_task_job_rows_for_connector(cid)
        for tbl, col in (
            ("connector_state", "connector_id"),
            ("file_state", "connector_id"),
        ):
            await self.infra.meta.execute(f"DELETE FROM {tbl} WHERE {col}=?", (cid,))
        await self.objects.delete_connector(cid)
        return True

    # --- read commands — any connector ---
    async def _match_connector(self, path: str) -> tuple[dict, str] | None:
        """Find the registered connector whose root is the longest prefix of `path`;
        return (connector_row, relpath) or None. Shared by _open_path (read commands)
        and resolve_connector_uri (search/grep scope). Thin delegate to
        ConnectorLocator.match; rows are fetched from ObjectRepository so the factory
        stays SQL-free."""
        rows = await self.objects.list_connectors_all()
        return ConnectorLocator.match(rows, path)

    async def _open_path(self, path: str):
        """(connector_id, connector_uri, relpath, plugin) for the registered connector
        whose root is the longest prefix of `path`. Thin delegate to
        ConnectorFactory.open_path."""
        rows = await self.objects.list_connectors_all()
        resolved = await self.connector_factory.open_path(rows, path)
        return resolved.cid, resolved.connector_uri, resolved.relpath, resolved.built.plugin

    async def ls(self, path: str) -> dict:
        """List children, each enriched with its full path + index state from the
        objects table, plus the connector's capabilities."""
        cid, curi, rel, plugin = await self._open_path(path)
        try:
            entries = await plugin.list(rel)
            caps = plugin.CAPABILITIES.to_dict()
        finally:
            await plugin.close()
        out = []
        base = rel.rstrip("/")
        for e in entries:
            child_rel = f"{base}/{e.name}" if base else "/" + e.name
            row = await self.objects.get_object_search_status(cid, child_rel)
            out.append(
                {
                    "name": e.name,
                    "type": e.type,
                    "media_type": e.media_type,
                    "size_hint": e.size_hint,
                    "path": curi + child_rel,
                    "search_status": row["search_status"] if row else None,
                    "indexable": (
                        bool(row["indexable"]) if row and row["indexable"] is not None else None
                    ),
                    "real_id": e.extra.get("real_id"),
                }
            )
        return {"entries": out, "capabilities": caps}

    @staticmethod
    def _locator_matches(rec: dict, ocfg, idx: int, locator: dict) -> bool:
        if "_row" in locator:
            return idx == int(locator["_row"])
        # "lines" is the framework-reserved key for body/code chunks and is never a
        # structured-record PK — never compare it against the row. The cat router
        # dispatches body-chunk reads through plugin.read(range=...) before reaching
        # this helper, so seeing it here is a misconfiguration we just ignore.
        keys = [k for k in (ocfg.locator_fields or list(locator.keys())) if k != "lines"]
        present = [k for k in keys if k in locator]
        # Require at least one recognized locator key: a locator that's empty or whose keys
        # don't correspond to this object's locator_fields matches nothing. Without this guard
        # `all([])` is True, so a bogus/typo'd locator silently returns record #0 instead of
        # the documented locator_not_found.
        if not present:
            return False
        # resolve with the SAME JSONPath-lite used to WRITE the locator (engine indexing:
        # {f: resolve_path(rec, f)}); plain rec.get() couldn't reopen a nested locator key.
        return all(str(resolve_path(rec, k)) == str(locator.get(k)) for k in present)

    async def cat(
        self,
        path: str,
        range: tuple[int, int] | None = None,
        meta: bool = False,
        density: str | None = None,
        locator: dict | None = None,
    ):
        import json as _json
        from contextlib import aclosing

        from ..connectors.base import Range

        cid, curi, rel, plugin = await self._open_path(path)
        try:
            st = await plugin.stat(rel)
            if st.type == "dir":
                raise IsADirectoryError(path)
            if meta:
                return {
                    "source": curi + rel,
                    "media_type": st.media_type,
                    "size_hint": st.size_hint,
                    "fingerprint": st.fingerprint,
                }
            okind = plugin.object_kind_of(rel)
            structured = okind in ("table_rows", "record_collection", "message_stream")
            # Binary objects have no line-based view — reading them with --range
            # would return mojibake (UTF-8 errors="replace") under the guise of a
            # text slice. Refuse cleanly so the caller falls back to `export`.
            if range is not None and okind == "binary":
                raise ValueError("range_unsupported")

            # --- locator with reserved "lines" key: route to the range path ---
            # Body / code / document chunks store identity as {"lines":[s,e]};
            # reopening one means slicing the file by line range, not iterating
            # structured records. locator.lines is 1-based half-open (matches
            # how cat --range is exposed); plugin.read takes 0-based half-open.
            if (
                locator is not None
                and isinstance(locator, dict)
                and "lines" in locator
                and len(locator) == 1
                and not structured
            ):
                s, e = int(locator["lines"][0]), int(locator["lines"][1])
                rg = Range(max(0, s - 1), max(0, e - 1))
                buf = bytearray()
                async for ch in plugin.read(rel, rg):
                    buf += ch
                return bytes(buf).decode("utf-8", errors="replace")

            # --- locator: reopen a single structured record ---
            if locator is not None:
                records = plugin.read_records(rel)
                if records is None:
                    raise ValueError("range_unsupported")  # not a structured object
                ocfg = plugin.ctx.object_config_for(rel)
                i = 0
                # aclosing: a match returns mid-iteration, so the record generator must be
                # closed deterministically — else a connector holding a cursor/transaction
                # (e.g. asyncpg) leaks the connection and pool.close() later blocks ~60s.
                async with aclosing(records):
                    async for rec in records:
                        if self._locator_matches(rec, ocfg, i, locator):
                            return {
                                "source": curi + rel,
                                "locator": locator,
                                "content": _json.dumps(rec, default=str, ensure_ascii=False),
                            }
                        i += 1
                raise ValueError("locator_not_found")

            # --- structured object: range pushdown over records (lazy, not materialized) ---
            if structured:
                if range is None:
                    # Bare cat of a structured object: stream the records as JSONL
                    # into a buffer up to _BARE_CAT_MAX_BYTES, then return. Small
                    # objects (Slack users.jsonl, small GitHub issue feeds, dozens-
                    # of-row tables) fit comfortably and round-trip as JSONL. Large
                    # ones (a postgres table with 1M rows) blow the budget mid-
                    # stream and raise the same object_too_large_for_cat so the
                    # caller still falls back to head / cat --range / export.
                    records = plugin.read_records(rel)
                    if records is None:
                        raise ValueError("object_too_large_for_cat")
                    budget = _BARE_CAT_MAX_BYTES
                    out: list[str] = []
                    size = 0
                    async with aclosing(records):
                        async for rec in records:
                            line = _json.dumps(rec, default=str, ensure_ascii=False)
                            size += len(line.encode("utf-8")) + 1  # +1 newline
                            if size > budget:
                                raise ValueError("object_too_large_for_cat")
                            out.append(line)
                    return "\n".join(out)
                start, end = range[0], range[1]
                # Pass Range(0, end) — a LIMIT-only hint — and slice [start, end) HERE, in one
                # place. Connectors disagree on whether they honor the Range: the DB ones
                # (mysql/postgres/mongo/bigquery) push OFFSET start + LIMIT down, while the SaaS
                # ones (jira/slack/notion/…) ignore it and return from row 0 — yet ALL declare
                # paged_cat=True, so the engine can't tell them apart. Pushing OFFSET start AND
                # then re-slicing `i >= start` double-applied the offset on the DB connectors, so
                # `cat --range 100:200` returned an empty/wrong page. With offset=0 every
                # connector returns rows from 0 and the single `i >= start` slice is correct for
                # both. (Trade-off: the DB connectors lose OFFSET pushdown and read `end` rows for
                # a deep page — still LIMIT-bounded; restoring true offset-pushdown needs an
                # explicit "range honored" capability — see human_todo [dborder/D65].)
                records = plugin.read_records(rel, Range(0, end))
                if records is not None:
                    out, i = [], 0
                    async with aclosing(records):  # break-early must close the generator
                        async for rec in records:
                            if i >= end:
                                break
                            if i >= start:
                                out.append(_json.dumps(rec, default=str, ensure_ascii=False))
                            i += 1
                    return "\n".join(out)

            ext = os.path.splitext(rel)[1].lower()
            text: str | None = None
            # converted markdown artifact: pdf/docx/html (CONVERT_EXTS) AND web/github pages,
            # whose .md is generated at ingest — read it from the artifact store so cat works
            # across restarts / fresh plugin instances, not just in-memory.
            if ext in CONVERT_EXTS:
                # On-read freshness: stat() above already fetched the live source fingerprint,
                # so comparing it to the one recorded at ingest is free. If it changed, the
                # cached markdown is stale -> re-convert from the current source.
                if await self._converted_md_stale(cid, rel, st.fingerprint):
                    raw = bytearray()
                    async for ch in plugin.read(rel):
                        raw += ch
                    text = await self.infra.converter.convert(bytes(raw), ext)
                else:
                    art = await self._read_artifact(self.ns, curi + rel, "converted_md")
                    if art is not None:
                        text = art.decode("utf-8", errors="replace")
            elif curi.startswith(("web://", "github://")):
                art = await self._read_artifact(self.ns, curi + rel, "converted_md")
                if art is not None:
                    text = art.decode("utf-8", errors="replace")
            if text is None and okind == "image" and self.cfg.description.enabled:
                # An image's description is a model output served through the transformation
                # cache: describe() returns the description memoized at ingest, or computes it
                # on a miss. (Re-reading the bytes here is the source round-trip — cheap for
                # local files; see TODO §10.9 for the snapshot path on remote connectors.)
                raw = bytearray()
                async for ch in plugin.read(rel):
                    raw += ch
                try:
                    return await self.infra.vlm.describe(bytes(raw), ext)
                except Exception:  # noqa: BLE001 — fall through to the raw read on provider error
                    pass
            if text is None:
                if range is None and st.size_hint and st.size_hint > _BARE_CAT_MAX_BYTES:
                    raise ValueError("object_too_large_for_cat")
                rg = Range(range[0], range[1]) if range else None
                buf = bytearray()
                async for ch in plugin.read(rel, rg):
                    buf += ch
                text = bytes(buf).decode("utf-8", errors="replace")
            if density and density != "deep":
                okind = plugin.object_kind_of(rel)
                if okind not in ("document", "code"):
                    raise ValueError("density_unsupported")
                return _density_view(text, ext, density)
            return text
        finally:
            await plugin.close()

    async def _read_full(self, path: str) -> tuple[str, bool]:
        """Whole object content for export / tail: returns (text, partial).
        partial=True when the connector capped the read (structured objects
        above max_read_rows). The bare-cat size guard is not applied. Backs
        export and tail; tail discards the partial flag (just wants the last
        N lines), export surfaces it."""
        import json as _json

        cid, curi, rel, plugin = await self._open_path(path)
        try:
            st = await plugin.stat(rel)
            if st.type == "dir":
                raise IsADirectoryError(path)
            okind = plugin.object_kind_of(rel)
            if okind in ("table_rows", "record_collection", "message_stream"):
                records = plugin.read_records(rel)
                if records is not None:
                    out = []
                    async for rec in records:
                        out.append(_json.dumps(rec, default=str, ensure_ascii=False))
                    text = "\n".join(out)
                    # ctx.declare_partial is the channel structured connectors use
                    # to flag "we capped at max_read_rows". Read it back here so
                    # export tells the truth instead of silently returning a slice.
                    partial = bool(getattr(plugin.ctx, "was_partial", lambda _r: False)(rel))
                    self._warn_if_huge_export(curi + rel, text)
                    return text, partial
            ext = os.path.splitext(rel)[1].lower()
            if ext in CONVERT_EXTS:
                # On-read freshness (same as cat): a changed source fingerprint means the
                # cached markdown is stale, so re-convert from the current source.
                if await self._converted_md_stale(cid, rel, st.fingerprint):
                    raw = bytearray()
                    async for ch in plugin.read(rel):
                        raw += ch
                    text = await self.infra.converter.convert(bytes(raw), ext)
                    self._warn_if_huge_export(curi + rel, text)
                    return text, False
                art = await self._read_artifact(self.ns, curi + rel, "converted_md")
                if art is not None:
                    text = art.decode("utf-8", errors="replace")
                    self._warn_if_huge_export(curi + rel, text)
                    return text, False
            elif curi.startswith(("web://", "github://")):
                art = await self._read_artifact(self.ns, curi + rel, "converted_md")
                if art is not None:
                    text = art.decode("utf-8", errors="replace")
                    self._warn_if_huge_export(curi + rel, text)
                    return text, False
            if okind == "image" and self.cfg.description.enabled:
                raw = bytearray()
                async for ch in plugin.read(rel):
                    raw += ch
                try:
                    text = await self.infra.vlm.describe(bytes(raw), ext)
                    self._warn_if_huge_export(curi + rel, text)
                    return text, False
                except Exception:  # noqa: BLE001 — fall through to the raw read on provider error
                    pass
            buf = bytearray()
            async for ch in plugin.read(rel):
                buf += ch
            text = bytes(buf).decode("utf-8", errors="replace")
            self._warn_if_huge_export(curi + rel, text)
            return text, False
        finally:
            await plugin.close()

    def _warn_if_huge_export(self, uri: str, text: str) -> None:
        """Single-host export materializes the whole object in memory; warn on anything over
        100 MB so the operator sees the cost before the next OOM rather than after. A streaming
        export path is the proper fix but is deferred — objects this large are rare on the
        single-host deployment this guard covers, and the warning makes the cost explicit."""
        size = len(text.encode("utf-8", errors="ignore")) if text else 0
        if size > 100 * 1024 * 1024:
            logger.warning(
                "export %s materialized %d MB in memory (streaming export not yet implemented)",
                uri,
                size // (1024 * 1024),
            )

    async def export(self, path: str) -> tuple[str, bool]:
        """Full content for `mfs export`: returns (text, partial). Honest
        boundary — structured connectors with more rows than max_read_rows
        return partial=True; the caller (API layer) surfaces it in the
        CatResponse. The bare-cat size guard does not apply, but each
        connector's own row cap does (true streaming export is deferred)."""
        return await self._read_full(path)

    async def head(self, path: str, n: int = 20) -> str:
        cid, curi, rel, plugin = await self._open_path(path)
        try:
            okind = plugin.object_kind_of(rel)
            structured = okind in ("table_rows", "record_collection", "message_stream")
            if structured:
                # fast path: pre-cached first rows. The cache is capped at _HEAD_CACHE_N, so
                # it's authoritative ONLY when it holds the whole object (< the cap) OR n fits
                # within it; for a larger n on a capped cache, fall through to the live bounded
                # query below — otherwise `head -n 200` would silently return just the 100
                # cached rows instead of 200 (head must give min(n, total), not min(n, cache)).
                art = await self._read_artifact(self.ns, curi + rel, "head_cache")
                if art is not None:
                    cached = art.decode("utf-8", errors="replace").splitlines()
                    if len(cached) < _HEAD_CACHE_N or n <= len(cached):
                        return "\n".join(cached[:n])
            else:
                ext = os.path.splitext(rel)[1].lower()
                # plain text / code / logs: stream just the first n lines so a large file
                # never materializes and never trips bare-cat's size guard — head is exactly
                # the escape hatch for big objects. Artifact-backed
                # objects (pdf/docx/html, web/github pages, images) have bounded cached text,
                # so they fall through to cat below.
                if not (
                    okind == "image"
                    or ext in CONVERT_EXTS
                    or curi.startswith(("web://", "github://"))
                ):
                    lines: list[str] = []
                    buf = b""
                    async for chunk in plugin.read(rel):
                        buf += chunk
                        while len(lines) < n:
                            nl = buf.find(b"\n")
                            if nl < 0:
                                break
                            lines.append(buf[:nl].decode("utf-8", errors="replace"))
                            buf = buf[nl + 1 :]
                        if len(lines) >= n:
                            break
                    if len(lines) < n and buf:
                        lines.append(buf.decode("utf-8", errors="replace"))
                    return "\n".join(lines[:n])
        finally:
            await plugin.close()
        if structured:
            return await self.cat(path, range=(0, n))  # bounded page, not the whole table
        text = await self.cat(path)  # artifact-backed text, bounded
        return "\n".join(text.splitlines()[:n])

    async def tail(self, path: str, n: int = 20) -> str:
        if n <= 0:
            return ""
        # plain-text real local file: read the last n lines straight off disk (native
        # accelerator / bounded reverse-read), so a huge log isn't fully materialized.
        # Artifact-backed (pdf/docx/html, web/github) and structured objects fall back.
        from ..common import accel

        cid, curi, rel, plugin = await self._open_path(path)
        try:
            okind = plugin.object_kind_of(rel)
            if okind in ("table_rows", "record_collection", "message_stream"):
                raise ValueError("tail_unsupported")
            ext = os.path.splitext(rel)[1].lower()
            plain_local = (
                curi.startswith("file://local")
                and okind
                not in (
                    "image",
                    "table_rows",
                    "record_collection",
                    "message_stream",
                    "table_schema",
                )
                and ext not in CONVERT_EXTS
            )
            abs_file = (curi.replace("file://local", "", 1) + rel) if plain_local else None
        finally:
            await plugin.close()
        if abs_file and os.path.isfile(abs_file):
            return "\n".join(await asyncio.to_thread(accel.tail_lines, abs_file, n))
        text, _partial = await self._read_full(
            path
        )  # artifact-backed / structured / non-local; tail ignores the partial flag
        return "\n".join(text.splitlines()[-n:])

    async def grep(
        self, pattern: str, path: str, top_k: int = 100, regex: bool = False
    ) -> list[dict]:
        """Dispatch: pushdown (file: none) -> BM25 (indexed scope) -> linear scan
        (not_indexed objects in scope). The linear scan uses the native
        accelerator (mfs_server_rs) when the object is a real local file, else falls
        back to reading bytes + pure-Python regex."""
        from ..common import accel
        from ..connectors.base import GrepOptions

        cid, curi, rel, plugin = await self._open_path(path)
        scope_prefix = (curi + rel) if rel != "/" else None
        try:
            # _open_path only resolves which connector owns the prefix, not whether
            # `rel` exists under it -- unlike ls/cat, nothing downstream fails loudly
            # for a missing path (pushdown/BM25/linear-scan all just yield zero
            # matches). Stat it explicitly so a bad path 404s like ls/cat instead of
            # looking like a real, empty search.
            await plugin.stat(rel)
            results: list[dict] = []
            # 2a connector grep pushdown: exact, source-side (e.g.
            # SQL ILIKE for structured connectors). Returns None when unsupported.
            ocfg = plugin.ctx.object_config_for(rel)
            try:
                gen = await plugin.grep(
                    pattern,
                    rel,
                    GrepOptions(
                        pattern=pattern,
                        text_fields=ocfg.text_fields,
                        metadata_fields=ocfg.metadata_fields,
                    ),
                )
            except Exception:  # noqa: BLE001 - pushdown failure shouldn't kill grep
                gen = None
            if gen is not None:
                async for gm in gen:
                    # Structured pushdown carries gm.locator (PK dict); text/code
                    # pushdown carries gm.line_no. locator.lines is 1-based
                    # half-open [s,e), so a single line n is [n, n+1] — not
                    # [n, n], which would round-trip as an empty slice.
                    loc = (
                        gm.locator
                        if gm.locator is not None
                        else ({"lines": [gm.line_no, gm.line_no + 1]} if gm.line_no else None)
                    )
                    results.append(
                        {
                            "source": curi + gm.path,
                            "locator": loc,
                            "content": gm.content,
                            "via": "pushdown",
                        }
                    )
                return results
            # 2b BM25 over indexed objects in scope
            expr = build_filter(self.ns, curi, scope_prefix)
            hits = await asyncio.to_thread(
                self.infra.milvus.sparse_search, self.ns, pattern, top_k, expr
            )
            for h in hits:
                e = h.get("entity", h)
                results.append(
                    {
                        "source": e.get("object_uri"),
                        "locator": e.get("locator"),
                        "content": e.get("content"),
                        "via": "bm25",
                    }
                )
            # 2c linear scan over not_indexed objects in scope (file connector)
            root_abs = (
                curi.replace("file://local", "", 1) if curi.startswith("file://local") else None
            )
            # Path-component boundary, same fix as build_filter: scope `/src` must match the
            # object itself OR `/src/...`, NOT a sibling `/src-old`. Escape SQL LIKE wildcards
            # ('_'/'%') in the literal prefix so a path with '_' doesn't over-match either.
            not_idx = await self.objects.list_not_indexed_in_scope(cid, rel)
            if len(not_idx) > _GREP_LINEAR_SCAN_MAX:
                # don't silently scan a subset and imply it was exhaustive — tell the agent
                # so it can narrow the path or index first.
                results.append(
                    {
                        "source": None,
                        "locator": None,
                        "via": "notice",
                        "content": f"(grep linear scan capped at {_GREP_LINEAR_SCAN_MAX} of "
                        f"{len(not_idx)} not-indexed files in scope; narrow the path "
                        f"or run `mfs add` to index them for complete results)",
                    }
                )
            for o in not_idx[:_GREP_LINEAR_SCAN_MAX]:
                relp = o["object_uri"]
                try:
                    abs_file = (root_abs + relp) if root_abs else None
                    if abs_file and os.path.isfile(abs_file):
                        # native (or pure-Python) streaming grep straight off disk
                        for ln, line in await asyncio.to_thread(
                            accel.linear_grep_file, abs_file, pattern, False, regex, 200
                        ):
                            results.append(
                                {
                                    "source": curi + relp,
                                    "locator": {"lines": [ln, ln + 1]},
                                    "content": line,
                                    "via": "linear",
                                }
                            )
                    else:
                        rx = re.compile(pattern if regex else re.escape(pattern))
                        buf = bytearray()
                        async for ch in plugin.read(relp):
                            buf += ch
                        text = bytes(buf).decode("utf-8", errors="replace")
                        for i, line in enumerate(text.splitlines(), 1):
                            if rx.search(line):
                                results.append(
                                    {
                                        "source": curi + relp,
                                        "locator": {"lines": [i, i + 1]},
                                        "content": line,
                                        "via": "linear",
                                    }
                                )
                except Exception:  # noqa: BLE001
                    pass
            return results
        finally:
            await plugin.close()
