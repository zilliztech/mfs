"""Engine: orchestration for `mfs add` (register connector -> job -> sync ->
object_tasks -> process). `_index_object` does the real per-object work: read ->
chunk/convert/VLM/summary -> embed -> Milvus upsert, per object_kind. Jobs run inline
(process=True) or are drained by the standalone worker (run_worker_*).

per-object atomic writes + job inheritance + circuit breaker (design/02 §6.4 §7.1).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone

from ..common.converter import CONVERT_EXTS, CachingConverterClient
from ..common.embedding import CachingEmbeddingClient
from ..common.retrieval import build_filter, collapse_by_object, to_envelope
from ..common.summary import CachingSummaryClient
from ..common.vlm import CachingVlmClient
from ..config import ServerConfig
from ..connectors.base import ConnectorContext, ObjectConfig, SyncOptions
from ..connectors.registry import get_plugin_cls, load_builtin
from ..processors.text import chunk_body
from ..storage.file_state import FileStateStore
from ..storage.ids import chunk_id
from ..storage.metadata import MetadataStore
from ..storage.milvus import MilvusStore
from ..storage.object_store import make_object_store
from ..storage.transformation_cache import TransformationCache
from .state import ConnectorStateStore

_SCHEME_RE = re.compile(r"^([a-z][a-z0-9+.\-]*)://")
_HEAD_CACHE_N = 100      # rows pre-cached per structured object to speed `head` (design/05)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _match_object_config(objects_cfg: list, path: str) -> ObjectConfig:
    """Find the [[objects]] entry whose `match` matches this path (design/06 §4),
    first-match wins; default ObjectConfig otherwise."""
    import fnmatch
    fields = ObjectConfig.__dataclass_fields__
    for o in objects_cfg:
        m = o.get("match", "")
        if m and (fnmatch.fnmatch(path, m) or fnmatch.fnmatch(path.lstrip("/"), m) or m in path):
            return ObjectConfig(**{k: v for k, v in o.items() if k != "match" and k in fields})
    return ObjectConfig()


_CODE_SYMBOL = re.compile(r"^\s*(def |class |func |fn |public |private |func\(|type )")


def _density_view(text: str, ext: str, density: str) -> str:
    """Skeleton view of a document/code object (design/05 §3):
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
                    for nxt in lines[i + 1:]:
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


class _SafeDict(dict):
    """format_map() helper: render unknown {field} placeholders as empty, not KeyError."""
    def __missing__(self, key):  # noqa: D401
        return ""


def _render_record(rec: dict, text_fields: list[str], template: str | None = None) -> str:
    """Render a record into chunk content (design/06 §4). With a text_template, do
    `{field}` substitution from the record (missing fields -> empty); otherwise join
    the configured text_fields with the default template."""
    if template:
        try:
            return template.format_map(_SafeDict(rec))
        except Exception:  # noqa: BLE001 - malformed template -> fall back to default join
            pass
    parts = []
    for f in text_fields:
        if "[]" in f or "[*]" in f:          # array field: comments[].body etc.
            base = f.split("[")[0]
            sub = f.split(".")[-1] if "." in f else None
            arr = rec.get(base) or []
            vals = [str(x.get(sub) if isinstance(x, dict) and sub else x) for x in arr]
            if vals:
                parts.append(f"{base}:\n- " + "\n- ".join(vals))
        else:
            v = rec.get(f)
            if v is not None:
                parts.append(f"{f}: {v}")
    return "\n\n".join(parts)


class Engine:
    def __init__(self, cfg: ServerConfig):
        self.cfg = cfg
        self.ns = cfg.namespace
        self.meta = MetadataStore(cfg)
        self.milvus = MilvusStore(cfg)
        self.object_store = make_object_store(cfg)
        self.tx_cache = TransformationCache(cfg)
        self.embed = CachingEmbeddingClient(cfg, self.tx_cache)
        self.converter = CachingConverterClient(cfg, self.tx_cache)
        self.vlm = CachingVlmClient(cfg, self.tx_cache)
        self.summary = CachingSummaryClient(cfg, self.tx_cache)
        self._artifact_writes = 0      # throttles LRU eviction sweeps (design/02 §10.2)

    async def startup(self) -> None:
        load_builtin()
        await self.meta.connect()
        await self.meta.init_schema()
        await self.tx_cache.connect()
        self.milvus.connect()
        self.milvus.ensure_collection(self.ns)

    async def shutdown(self) -> None:
        await self.meta.close()
        await self.tx_cache.close()

    # --- target resolution (Phase 2: file only) ---
    def _resolve_target(self, target: str) -> tuple[str, str, str, dict]:
        m = _SCHEME_RE.match(target)
        if m:
            sch = m.group(1)
            if sch in ("web", "github", "postgres", "mysql", "mongo",
                       "slack", "discord", "gmail", "notion", "jira", "linear",
                       "zendesk", "salesforce", "hubspot", "bigquery", "snowflake",
                       "s3", "gdrive", "feishu"):
                return sch, target, sch, {}
            if sch != "file":
                raise NotImplementedError(f"connector scheme '{sch}' not yet implemented")
        # local path -> file connector
        abs_path = os.path.abspath(target)
        connector_uri = f"file://local{abs_path}"
        return "file", connector_uri, "file", {"root": abs_path, "client_id": "local"}

    async def register_or_get_connector(self, connector_uri: str, ctype: str, config: dict,
                                         overwrite_config: bool = False) -> str:
        import json
        row = await self.meta.fetchone(
            "SELECT id FROM connectors WHERE namespace_id=? AND root_uri=?", (self.ns, connector_uri))
        if row:
            # `mfs connector update --config` re-registers an existing connector: refresh
            # its stored config so changed text_fields / scope / credential_ref take effect.
            if overwrite_config:
                await self.meta.execute(
                    "UPDATE connectors SET config_json=? WHERE id=?", (json.dumps(config), row["id"]))
            return row["id"]
        cid = uuid.uuid4().hex
        await self.meta.execute(
            "INSERT INTO connectors (id, namespace_id, root_uri, type, status, config_json, registered_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (cid, self.ns, connector_uri, ctype, "active", json.dumps(config), _now()))
        return cid

    @staticmethod
    def _resolve_ref(v):
        """Resolve an `env:VAR` reference to its environment value (design/07 credential
        ref). Non-ref values pass through unchanged."""
        if isinstance(v, str) and v.startswith("env:"):
            return os.environ.get(v[4:], "")
        return v

    def _build_plugin(self, ctype: str, config: dict, connector_id: str):
        cls = get_plugin_cls(ctype)
        if cls is None:
            raise NotImplementedError(f"no plugin for {ctype}")
        # Resolve credential references at build time so secrets live in the environment,
        # not in connectors.config_json (design/07). The stored config keeps the `env:VAR`
        # ref / `_credential_ref`; only this in-memory copy carries resolved values.
        credential = None
        if isinstance(config, dict):
            config = {k: self._resolve_ref(v) for k, v in config.items()}
            # design name is `credential_ref`; accept `_credential_ref` as a legacy alias
            cred_a = config.pop("credential_ref", None)
            cred_b = config.pop("_credential_ref", None)
            credential = cred_a if cred_a is not None else cred_b
        objects_cfg = config.get("objects", []) if isinstance(config, dict) else []
        state = ConnectorStateStore(self.meta, connector_id)
        ctx = ConnectorContext(state, connector_id, self.ns,
                               object_config_resolver=lambda p: _match_object_config(objects_cfg, p))
        if ctype == "file":
            from ..connectors.file.plugin import FileConfig
            plugin = cls(FileConfig(root=config["root"], client_id=config.get("client_id", "local")),
                         credential, ctx=ctx)
            plugin.file_state = FileStateStore(self.meta, self.ns, connector_id)
        else:
            plugin = cls(config, credential, ctx=ctx)
        return plugin, ctx

    # --- add (register + sync + worker) ---
    async def add(self, target: str, config: dict | None = None, full: bool = False,
                  since: str | None = None, process: bool = True) -> str:
        """Register + sync + enqueue tasks. process=True (AIO default): run the job
        inline and return when done. process=False: leave the job 'queued' for a
        standalone worker (design/02 §5) to pick up via run_worker_*()."""
        import json
        _, connector_uri, ctype, default_config = self._resolve_target(target)
        # --since requires a time cursor; reject early on connectors without one (errors.md)
        if since:
            cls = get_plugin_cls(ctype)
            if cls is not None and not getattr(cls.CAPABILITIES, "cursor_kind", None):
                raise ValueError("since_unsupported")
        cfg_dict = config if config is not None else default_config
        cid = await self.register_or_get_connector(connector_uri, ctype, cfg_dict,
                                                   overwrite_config=config is not None)
        row0 = await self.meta.fetchone("SELECT config_json FROM connectors WHERE id=?", (cid,))
        stored_cfg = json.loads(row0["config_json"]) if row0 and row0["config_json"] else cfg_dict

        job_id = uuid.uuid4().hex
        await self.meta.execute(
            "INSERT INTO connector_jobs (id, namespace_id, connector_id, op_kind, trigger, status, "
            " started_at, heartbeat) VALUES (?,?,?,?,?,?,?,?)",
            (job_id, self.ns, cid, "sync", "manual", "running" if process else "queued", _now(), _now()))

        # job inheritance: take over this connector's leftover pending/failed tasks (design/02 §7.1)
        await self.meta.execute(
            "UPDATE object_tasks SET connector_job_id=?, status='pending' "
            "WHERE connector_id=? AND status IN ('pending','failed') AND attempts < ?",
            (job_id, cid, self.cfg.worker.max_retries))

        plugin, ctx = self._build_plugin(ctype, stored_cfg, cid)
        await plugin.connect()
        aborted: str | None = None
        try:
            opts = SyncOptions(full=full, since=since)
            async for ch in plugin.sync(opts):
                # deletion safety (design/02 §7.4): only honor full-set diff deletes when
                # the connector declared a complete enumeration this run; on incremental /
                # explicit_only a "missing" object is unknown, not deleted.
                if ch.kind == "deleted" and ctx.enumeration_mode != "full":
                    continue
                tid = uuid.uuid4().hex
                await self.meta.execute(
                    "INSERT INTO object_tasks (id, connector_job_id, connector_id, object_uri, old_uri, "
                    " change_kind, status, priority, attempts) VALUES (?,?,?,?,?,?,?,?,0)",
                    (tid, job_id, cid, ch.uri, ch.old_uri, ch.kind, "pending", plugin.task_priority(ch)))
            if not process:
                # enqueue model: a standalone worker (separate process) won't re-run
                # sync, so the enumerated cursor must persist now. Work isn't lost on a
                # later failure — tasks are durable and job inheritance retries them.
                await ctx.state.commit()
                return job_id
            aborted = await self._run_job(job_id, cid, connector_uri, plugin)
        finally:
            await plugin.close()

        # commit connector cursor/state only after the inline job actually succeeded
        # (design/02 §7 ③): a mid-pipeline failure (embed/Milvus) must not advance the
        # cursor past objects that never got indexed.
        if aborted is None:
            await ctx.state.commit()
        await self._finalize_job(job_id, aborted)
        return job_id

    async def ingest_upload(self, name: str, data: bytes, fmt: str = "tar",
                            process: bool = True) -> dict:
        """CS upload flow (design/02 §4.2): client/server don't share a fs, so the
        client ships a tar(.gz) of the tree; the server extracts it into a per-upload
        staging dir under the object store and indexes that dir with the file connector.
        Guards against path traversal (zip-slip)."""
        import hashlib
        import io
        import tarfile

        sub = hashlib.sha1(name.encode()).hexdigest()[:16]
        staging = self.object_store.files_root(self.ns, sub)
        staging_str = os.path.realpath(str(staging))
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
            members = tf.getmembers()
            for m in members:
                if m.issym() or m.islnk():
                    raise ValueError(f"links not allowed in upload: {m.name}")
                dest = os.path.realpath(os.path.join(staging_str, m.name))
                if dest != staging_str and not dest.startswith(staging_str + os.sep):
                    raise ValueError(f"unsafe path in archive: {m.name}")
            tf.extractall(staging_str)        # validated above
        job_id = await self.add(staging_str, process=process)
        _, connector_uri, _, _ = self._resolve_target(staging_str)
        return {"job_id": job_id, "connector_uri": connector_uri, "staging": staging_str}

    async def _finalize_job(self, job_id: str, aborted: str | None) -> None:
        """Set terminal job status + per-status object counts (design/02 §7)."""
        counts = await self.meta.fetchall(
            "SELECT status, count(*) AS n FROM object_tasks WHERE connector_job_id=? GROUP BY status", (job_id,))
        cmap = {r["status"]: r["n"] for r in counts}
        jrow = await self.meta.fetchone("SELECT status FROM connector_jobs WHERE id=?", (job_id,))
        if jrow and jrow["status"] == "cancelled":
            status = "cancelled"
        elif aborted:
            status = "failed"
        else:
            status = "succeeded"
        await self.meta.execute(
            "UPDATE connector_jobs SET status=?, finished_at=?, error=?, "
            " total_objects=?, succeeded_objects=?, failed_objects=?, cancelled_objects=? WHERE id=?",
            (status, _now(), aborted,
             sum(cmap.values()), cmap.get("succeeded", 0), cmap.get("failed", 0),
             cmap.get("cancelled", 0), job_id))

    # --- standalone worker (design/02 §5): poll DB queue, process queued jobs ---
    async def cancel_job(self, job_id: str) -> bool:
        """Cancel a job: mark it + its pending/running tasks cancelled. A running
        worker stops at the next per-object boundary (checked in _run_job)."""
        row = await self.meta.fetchone("SELECT status FROM connector_jobs WHERE id=?", (job_id,))
        if not row or row["status"] in ("succeeded", "failed", "cancelled"):
            return False
        await self.meta.execute(
            "UPDATE object_tasks SET status='cancelled' "
            "WHERE connector_job_id=? AND status IN ('pending','running')", (job_id,))
        await self.meta.execute(
            "UPDATE connector_jobs SET status='cancelled', finished_at=? WHERE id=?", (_now(), job_id))
        return True

    async def _claim_queued_job(self) -> dict | None:
        """Atomically claim the oldest queued job. Multi-worker safe: the claim is a
        conditional UPDATE guarded on status='queued', and we take the job only when
        *this* worker's UPDATE flipped the row (rowcount == 1). Two workers racing the
        same job -> only one's UPDATE matches; the loser tries the next candidate."""
        candidates = await self.meta.fetchall(
            "SELECT * FROM connector_jobs WHERE status='queued' ORDER BY started_at LIMIT 8")
        for row in candidates:
            won = await self.meta.execute_rowcount(
                "UPDATE connector_jobs SET status='running', heartbeat=? WHERE id=? AND status='queued'",
                (_now(), row["id"]))
            if won == 1:
                return row
        return None

    async def run_worker_once(self) -> str | None:
        """Claim + process one queued job. Returns its id, or None if queue empty."""
        import json
        job = await self._claim_queued_job()
        if not job:
            return None
        cid = job["connector_id"]
        crow = await self.meta.fetchone("SELECT root_uri, type, config_json FROM connectors WHERE id=?", (cid,))
        connector_uri, ctype = crow["root_uri"], crow["type"]
        stored_cfg = json.loads(crow["config_json"]) if crow["config_json"] else {}
        plugin, _ = self._build_plugin(ctype, stored_cfg, cid)
        await plugin.connect()
        aborted: str | None = None
        try:
            aborted = await self._run_job(job["id"], cid, connector_uri, plugin)
        finally:
            await plugin.close()
        await self._finalize_job(job["id"], aborted)
        return job["id"]

    def _resolve_concurrency(self, concurrency=None) -> int:
        c = concurrency if concurrency is not None else self.cfg.worker.concurrency
        if c == "auto":
            return max(1, (os.cpu_count() or 2))
        try:
            return max(1, int(c))
        except (TypeError, ValueError):
            return 1

    async def _reclaim_stale_jobs(self, stale_after_s: int = 120) -> None:
        """Housekeeping (design/02 §5): a job whose worker died keeps status='running'
        with a stale heartbeat forever. Reset such jobs to 'queued' so a live worker
        re-claims them. Best-effort — tolerate the rare one-queued-per-connector clash."""
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=stale_after_s)).isoformat()
        try:
            await self.meta.execute(
                "UPDATE connector_jobs SET status='queued' "
                "WHERE status='running' AND heartbeat IS NOT NULL AND heartbeat < ?", (cutoff,))
        except Exception:  # noqa: BLE001
            pass

    async def run_worker_forever(self, poll_interval: float = 1.0, concurrency=None) -> None:
        """Drain the queued-job queue with `concurrency` parallel workers. Each worker
        atomically claims a distinct job (the conditional claim is race-free), so N
        connectors' sync jobs run in parallel. Idle workers run a housekeeping pass that
        reclaims jobs orphaned by a crashed worker (stale heartbeat)."""
        n = self._resolve_concurrency(concurrency)

        async def _loop() -> None:
            while True:
                jid = await self.run_worker_once()
                if jid is None:
                    await self._reclaim_stale_jobs()
                    await asyncio.sleep(poll_interval)

        await asyncio.gather(*[_loop() for _ in range(n)])

    async def _claim_batch(self, job_id: str, limit: int) -> list[dict]:
        """Claim up to `limit` pending tasks. Each is taken with a conditional UPDATE
        guarded on status='pending'; only rows this worker actually flipped (rowcount
        == 1) are returned, so concurrent workers never double-process a task."""
        rows = await self.meta.fetchall(
            "SELECT * FROM object_tasks WHERE connector_job_id=? AND status='pending' "
            "ORDER BY priority ASC, started_at ASC LIMIT ?", (job_id, limit))
        claimed = []
        for r in rows:
            won = await self.meta.execute_rowcount(
                "UPDATE object_tasks SET status='running', started_at=?, attempts=attempts+1 "
                "WHERE id=? AND status='pending'", (_now(), r["id"]))
            if won == 1:
                claimed.append(r)
        return claimed

    @staticmethod
    def _classify_error(e: Exception) -> str:
        """retryable (transient: 429 rate-limit / 5xx / timeout) vs fatal (structural:
        quota exhausted / auth) — design/02 §7.1."""
        m = str(e).lower()
        fatal_markers = ("insufficient_quota", "quota", "invalid_api_key", "authentication",
                         "unauthorized", "402", "401", "permission denied", "invalid x-api-key")
        if any(k in m for k in fatal_markers):
            return "fatal"
        nm = type(e).__name__.lower()
        if "authentication" in nm or "permissiondenied" in nm:
            return "fatal"
        return "retryable"

    async def _process_with_retry(self, plugin, connector_uri: str, task: dict) -> str | None:
        """Returns None on success, 'fatal', or 'retryable_exhausted'."""
        import asyncio as _a
        max_r = self.cfg.worker.max_retries
        for attempt in range(max_r + 1):
            try:
                await self._index_object(plugin, connector_uri, task)
                return None
            except Exception as e:  # noqa: BLE001
                kind = self._classify_error(e)
                if kind == "fatal":
                    await self.meta.execute(
                        "UPDATE object_tasks SET status='failed', finished_at=?, last_error=? WHERE id=?",
                        (_now(), f"fatal: {e}", task["id"]))
                    return "fatal"
                if attempt < max_r:
                    await _a.sleep(self.cfg.worker.backoff_initial_ms / 1000)
                    continue
                await self.meta.execute(
                    "UPDATE object_tasks SET status='failed', finished_at=?, last_error=? WHERE id=?",
                    (_now(), str(e), task["id"]))
                return "retryable_exhausted"
        return "retryable_exhausted"

    async def _run_job(self, job_id: str, cid: str, connector_uri: str, plugin) -> str | None:
        """Returns None on normal completion, or a circuit-breaker reason string.
        Consecutive fatal failures (design/02 §7.1) abort the job."""
        threshold = self.cfg.worker.consecutive_fatal_threshold
        consec_fatal = 0
        while True:
            # per-object cancel boundary: stop if the job was cancelled externally
            jrow = await self.meta.fetchone("SELECT status FROM connector_jobs WHERE id=?", (job_id,))
            if jrow and jrow["status"] == "cancelled":
                return "cancelled"
            # heartbeat so housekeeping doesn't reclaim a job this worker is actively running
            await self.meta.execute(
                "UPDATE connector_jobs SET heartbeat=? WHERE id=?", (_now(), job_id))
            tasks = await self._claim_batch(job_id, limit=64)
            if not tasks:
                break
            for t in tasks:
                r = await self._process_with_retry(plugin, connector_uri, t)
                if r is None:
                    await self.meta.execute(
                        "UPDATE object_tasks SET status='succeeded', finished_at=? WHERE id=?",
                        (_now(), t["id"]))
                    consec_fatal = 0
                elif r == "fatal":
                    consec_fatal += 1
                    if consec_fatal >= threshold:
                        await self.meta.execute(
                            "UPDATE object_tasks SET status='cancelled' "
                            "WHERE connector_job_id=? AND status IN ('pending','running')",
                            (job_id,))
                        return "circuit_breaker_tripped"
                else:
                    consec_fatal = 0
        return None

    async def _read_text(self, plugin, relpath: str) -> str:
        return (await self._read_bytes(plugin, relpath)).decode("utf-8", errors="replace")

    async def _read_bytes(self, plugin, relpath: str) -> bytes:
        buf = bytearray()
        async for chunk in plugin.read(relpath):
            buf += chunk
        return bytes(buf)

    # --- artifact cache (design/02 §10.2): bytes in the object store + a metadata row
    #     in artifact_cache, with LRU size eviction ---
    async def _put_artifact(self, ns: str, object_uri: str, kind: str, data: bytes) -> str:
        """Store artifact bytes and record/refresh its artifact_cache row (size +
        content fingerprint + timestamps), then run a throttled LRU sweep so the cache
        stays under budget. fingerprint = sha1(bytes) — lets a re-build detect a
        no-op (same content) and gives a stale-check handle (design/02 §10.2)."""
        import hashlib
        path = await asyncio.to_thread(self.object_store.put_artifact, ns, object_uri, kind, data)
        now = _now()
        fp = hashlib.sha1(data).hexdigest()
        await self.meta.execute(
            "INSERT INTO artifact_cache (namespace_id, object_uri, artifact_kind, storage_path, "
            " fingerprint, size_bytes, built_at, last_accessed) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(namespace_id, object_uri, artifact_kind) DO UPDATE SET "
            " storage_path=excluded.storage_path, fingerprint=excluded.fingerprint, "
            " size_bytes=excluded.size_bytes, built_at=excluded.built_at, last_accessed=excluded.last_accessed",
            (ns, object_uri, kind, str(path), fp, len(data), now, now))
        self._artifact_writes += 1
        if self._artifact_writes % 16 == 0:
            await self._evict_artifacts_if_needed(ns)
        return path

    async def _drop_artifacts(self, ns: str, object_uri: str) -> None:
        """Delete all cached artifacts of an object (bytes + artifact_cache rows) — on
        object deletion so the cache doesn't retain orphaned bytes (design/02 §10.2)."""
        for kind in ("converted_md", "vlm_text", "head_cache"):
            try:
                await asyncio.to_thread(self.object_store.delete_artifact, ns, object_uri, kind)
            except Exception:  # noqa: BLE001
                pass
        await self.meta.execute(
            "DELETE FROM artifact_cache WHERE namespace_id=? AND object_uri=?", (ns, object_uri))

    async def _read_artifact(self, ns: str, object_uri: str, kind: str) -> bytes | None:
        """Fetch artifact bytes and bump last_accessed (LRU recency) when present."""
        data = await asyncio.to_thread(self.object_store.get_artifact, ns, object_uri, kind)
        if data is not None:
            await self.meta.execute(
                "UPDATE artifact_cache SET last_accessed=? "
                "WHERE namespace_id=? AND object_uri=? AND artifact_kind=?",
                (_now(), ns, object_uri, kind))
        return data

    async def _evict_artifacts_if_needed(self, ns: str) -> int:
        """Evict least-recently-accessed artifacts until total bytes fall under
        artifact_cache.max_size_gb. Returns the number evicted."""
        max_bytes = int(self.cfg.artifact_cache.max_size_gb * (1 << 30))
        row = await self.meta.fetchone(
            "SELECT sum(size_bytes) AS total FROM artifact_cache WHERE namespace_id=?", (ns,))
        total = (row and row["total"]) or 0
        if total <= max_bytes:
            return 0
        victims = await self.meta.fetchall(
            "SELECT object_uri, artifact_kind, size_bytes FROM artifact_cache "
            "WHERE namespace_id=? ORDER BY last_accessed ASC", (ns,))
        evicted = 0
        for v in victims:
            if total <= max_bytes:
                break
            try:
                await asyncio.to_thread(self.object_store.delete_artifact, ns,
                                        v["object_uri"], v["artifact_kind"])
            except Exception:  # noqa: BLE001
                pass
            await self.meta.execute(
                "DELETE FROM artifact_cache WHERE namespace_id=? AND object_uri=? AND artifact_kind=?",
                (ns, v["object_uri"], v["artifact_kind"]))
            total -= v["size_bytes"] or 0
            evicted += 1
        return evicted

    async def _index_object(self, plugin, connector_uri: str, task: dict) -> None:
        """Real chunk/embed/Milvus (design/04 §2 ⑤). document/code -> body chunks;
        other kinds carry no chunks in Phase 3 (image VLM / pdf converter -> Phase 6).
        per-object atomic: delete_by_object then upsert all of this object's chunks."""
        relpath = task["object_uri"]
        kind = task["change_kind"]
        cid = task["connector_id"]
        ns = self.ns
        full_uri = connector_uri + relpath

        if kind == "deleted":
            await self.meta.execute(
                "DELETE FROM objects WHERE connector_id=? AND object_uri=?", (cid, relpath))
            await asyncio.to_thread(self.milvus.delete_by_object, ns, connector_uri, full_uri)
            await self._drop_artifacts(ns, full_uri)      # purge cached artifact bytes too
            await plugin.on_object_deleted(relpath)
            return

        if kind == "renamed" and task["old_uri"]:
            old_full = connector_uri + task["old_uri"]
            # rename = chunk_id rewrite, REUSE vectors (zero re-embed; design/04 §5.7.3)
            old_chunks = await asyncio.to_thread(self.milvus.get_chunks_by_object, ns, connector_uri, old_full)
            if old_chunks:
                rows = []
                for ch in old_chunks:
                    loc, ln = ch.get("locator"), ch.get("lines")
                    rows.append({
                        "chunk_id": chunk_id(ns, connector_uri, full_uri, ch["chunk_kind"], loc, ln),
                        "namespace_id": ns, "connector_uri": connector_uri, "object_uri": full_uri,
                        "locator": loc, "lines": ln, "content": ch["content"], "dense_vec": ch["dense_vec"],
                        "chunk_kind": ch["chunk_kind"], "metadata": ch.get("metadata") or {},
                        "indexed_at": ch.get("indexed_at") or int(time.time() * 1000)})
                await asyncio.to_thread(self.milvus.delete_by_object, ns, connector_uri, old_full)
                await asyncio.to_thread(self.milvus.upsert, ns, rows)
                await asyncio.to_thread(self.object_store.move_artifacts, ns, old_full, full_uri)
                st = await plugin.stat(relpath)
                await self.meta.execute("DELETE FROM objects WHERE connector_id=? AND object_uri=?", (cid, task["old_uri"]))
                await self.meta.execute(
                    "INSERT INTO objects (connector_id, object_uri, parent_path, type, media_type, size_hint, "
                    " fingerprint, indexable, last_seen, search_status, chunk_count, indexed_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(connector_id, object_uri) DO UPDATE SET "
                    " type=excluded.type, media_type=excluded.media_type, size_hint=excluded.size_hint, "
                    " fingerprint=excluded.fingerprint, indexable=excluded.indexable, last_seen=excluded.last_seen, "
                    " search_status=excluded.search_status, chunk_count=excluded.chunk_count, indexed_at=excluded.indexed_at",
                    (cid, relpath, os.path.dirname(relpath) or "/", st.type, st.media_type, st.size_hint,
                     st.fingerprint, 1, _now(), "indexed", len(rows), _now()))
                await plugin.on_object_indexed(relpath)
                return    # reused vectors — no chunk/embed
            # fallback (old had no chunks): drop refs, index new normally below
            await asyncio.to_thread(self.milvus.delete_by_object, ns, connector_uri, old_full)
            await self.meta.execute(
                "DELETE FROM objects WHERE connector_id=? AND object_uri=?", (cid, task["old_uri"]))

        st = await plugin.stat(relpath)
        okind = plugin.object_kind_of(relpath)
        chunk_count = 0
        search_status = "not_indexed"
        top_cfg = plugin.ctx.object_config_for(relpath)
        # `indexable` is binary-vs-not by object_kind, AND can be opted out per
        # [[objects]] config (design/06 §4 indexable=false): record the object so it
        # shows in ls/inspect, but skip all chunk/embed/Milvus work.
        indexable = okind not in ("binary",) and top_cfg.indexable

        if not indexable:
            pass        # binary / opted-out: metadata-only, no chunk/embed (gated below)
        elif okind in ("document", "code"):
            ext = os.path.splitext(relpath)[1].lower()
            if okind == "document" and ext in CONVERT_EXTS:
                raw = await self._read_bytes(plugin, relpath)
                text = await self.converter.convert(raw, ext)
                await self._put_artifact(ns, full_uri, "converted_md", text.encode())
            else:
                text = await self._read_text(plugin, relpath)
                if connector_uri.startswith(("web://", "github://")):
                    await self._put_artifact(ns, full_uri, "converted_md", text.encode())
            ocfg = plugin.ctx.object_config_for(relpath)
            pairs = chunk_body(text, okind, ext, self.cfg.chunk.chunk_size)
            chunk_max = ocfg.chunk_max
            partial = len(pairs) > chunk_max
            if partial:
                pairs = pairs[:chunk_max]
            if pairs:
                vecs = await self.embed.batch_embed([p[0] for p in pairs])
                now_ms = int(time.time() * 1000)
                rows = []
                for (ctext, lines), vec in zip(pairs, vecs):
                    rows.append({
                        "chunk_id": chunk_id(ns, connector_uri, full_uri, "body", None, lines),
                        "namespace_id": ns, "connector_uri": connector_uri, "object_uri": full_uri,
                        "locator": None, "lines": lines, "content": ctext[:65000],
                        "dense_vec": vec, "chunk_kind": "body", "metadata": {}, "indexed_at": now_ms,
                    })
                # extra whole-object `summary` chunk for large docs (design/06 §6): one
                # condensed chunk improves recall for holistic queries.
                if self.summary.should_summarize(text):
                    summ = await self.summary.summarize(text, "summary")
                    if summ.strip():
                        svec = (await self.embed.batch_embed([summ]))[0]
                        rows.append({
                            "chunk_id": chunk_id(ns, connector_uri, full_uri, "summary", None, None),
                            "namespace_id": ns, "connector_uri": connector_uri, "object_uri": full_uri,
                            "locator": None, "lines": None, "content": summ[:65000],
                            "dense_vec": svec, "chunk_kind": "summary", "metadata": {}, "indexed_at": now_ms,
                        })
                await asyncio.to_thread(self.milvus.delete_by_object, ns, connector_uri, full_uri)
                await asyncio.to_thread(self.milvus.upsert, ns, rows)
                chunk_count = len(rows)
                search_status = "partial" if partial else "indexed"

        elif okind == "image":
            ext = os.path.splitext(relpath)[1].lower()
            raw = await self._read_bytes(plugin, relpath)
            desc = await self.vlm.describe(raw, ext)
            await self._put_artifact(ns, full_uri, "vlm_text", desc.encode())
            if desc.strip():
                vec = (await self.embed.batch_embed([desc]))[0]
                row = {
                    "chunk_id": chunk_id(ns, connector_uri, full_uri, "vlm_description", None, None),
                    "namespace_id": ns, "connector_uri": connector_uri, "object_uri": full_uri,
                    "locator": None, "lines": None, "content": desc[:65000], "dense_vec": vec,
                    "chunk_kind": "vlm_description", "metadata": {}, "indexed_at": int(time.time() * 1000),
                }
                await asyncio.to_thread(self.milvus.delete_by_object, ns, connector_uri, full_uri)
                await asyncio.to_thread(self.milvus.upsert, ns, [row])
                chunk_count = 1
                search_status = "indexed"

        elif okind == "message_stream":
            ocfg = plugin.ctx.object_config_for(relpath)
            records = plugin.read_records(relpath)
            if records is not None and ocfg.text_fields:
                # per_group thread_aggregate: group messages by group_by, each thread/
                # group becomes one aggregate chunk (design/06 §2 thread_aggregate). When
                # not configured, fall back to common thread keys across connectors
                # (slack thread_ts / gmail threadId / generic thread_id).
                cfg_key = ocfg.group_by
                group_key = cfg_key or "thread"
                _THREAD_KEYS = ("thread_ts", "threadId", "thread_id", "thread")
                groups: dict = {}
                order: list = []
                async for rec in records:
                    if cfg_key:
                        gk = rec.get(cfg_key)
                    else:
                        gk = next((rec[k] for k in _THREAD_KEYS if rec.get(k)), None)
                    gk = gk or rec.get("ts") or rec.get("id") or str(len(order))
                    if gk not in groups:
                        groups[gk] = []
                        order.append(gk)
                    groups[gk].append(rec)
                    if len(order) >= ocfg.chunk_max:
                        break
                pairs: list[tuple[str, dict | None]] = []
                for gk in order:
                    body = "\n\n".join(
                        _render_record(m, ocfg.text_fields, ocfg.text_template) for m in groups[gk])
                    body = body.strip()
                    if body:
                        pairs.append((body[: (ocfg.max_text_chars or 200000)], {group_key: gk}))
                if pairs:
                    vecs = await self.embed.batch_embed([p[0] for p in pairs])
                    now_ms = int(time.time() * 1000)
                    rows = [{
                        "chunk_id": chunk_id(ns, connector_uri, full_uri, "thread_aggregate", loc, None),
                        "namespace_id": ns, "connector_uri": connector_uri, "object_uri": full_uri,
                        "locator": loc, "lines": None, "content": ctext[:65000], "dense_vec": vec,
                        "chunk_kind": "thread_aggregate", "metadata": {}, "indexed_at": now_ms,
                    } for (ctext, loc), vec in zip(pairs, vecs)]
                    await asyncio.to_thread(self.milvus.delete_by_object, ns, connector_uri, full_uri)
                    await asyncio.to_thread(self.milvus.upsert, ns, rows)
                    chunk_count = len(rows)
                    search_status = "indexed"

        elif okind in ("table_rows", "record_collection"):
            ocfg = plugin.ctx.object_config_for(relpath)
            records = plugin.read_records(relpath)
            if records is not None and ocfg.text_fields:
                predicate = None
                if ocfg.index_filter:
                    from ..common.filter_ast import compile_filter
                    predicate = compile_filter(ocfg.index_filter)   # restricted AST, not eval
                pairs: list[tuple[str, dict | None, dict]] = []
                head_buf: list[str] = []        # first N raw records -> head_cache artifact
                partial = False
                i = 0
                async for rec in records:
                    if len(head_buf) < _HEAD_CACHE_N:
                        head_buf.append(json.dumps(rec, default=str, ensure_ascii=False))
                    if predicate is not None and not predicate(rec):
                        i += 1
                        continue        # row excluded by index_filter
                    text = _render_record(rec, ocfg.text_fields, ocfg.text_template)
                    if text.strip():
                        loc = {f: rec.get(f) for f in ocfg.locator_fields} if ocfg.locator_fields else {"_row": i}
                        # carry configured metadata_fields onto the chunk so search hits
                        # surface them in metadata.fields (design/06 §4 metadata_fields)
                        meta = {f: rec.get(f) for f in ocfg.metadata_fields} if ocfg.metadata_fields else {}
                        pairs.append((text, loc, meta))
                    i += 1
                    if len(pairs) >= ocfg.chunk_max:
                        partial = True
                        break
                if head_buf:        # pre-cache first rows so `head` is fast without re-querying
                    await self._put_artifact(ns, full_uri, "head_cache", ("\n".join(head_buf)).encode())
                if pairs:
                    vecs = await self.embed.batch_embed([p[0] for p in pairs])
                    now_ms = int(time.time() * 1000)
                    rows = [{
                        "chunk_id": chunk_id(ns, connector_uri, full_uri, "row_text", loc, None),
                        "namespace_id": ns, "connector_uri": connector_uri, "object_uri": full_uri,
                        "locator": loc, "lines": None, "content": ctext[:65000], "dense_vec": vec,
                        "chunk_kind": "row_text", "metadata": meta, "indexed_at": now_ms,
                    } for (ctext, loc, meta), vec in zip(pairs, vecs)]
                    await asyncio.to_thread(self.milvus.delete_by_object, ns, connector_uri, full_uri)
                    await asyncio.to_thread(self.milvus.upsert, ns, rows)
                    chunk_count = len(rows)
                    search_status = "partial" if partial else "indexed"

        elif okind == "table_schema" and self.summary.enabled != "false":
            # schema_summary chunk: an LLM description of the table/collection schema
            records = plugin.read_records(relpath)
            schema_obj = None
            if records is not None:
                async for r in records:
                    schema_obj = r
                    break
            if schema_obj is not None:
                import json as _json
                summ = await self.summary.summarize(_json.dumps(schema_obj, default=str), "schema_summary")
                if summ.strip():
                    vec = (await self.embed.batch_embed([summ]))[0]
                    row = {
                        "chunk_id": chunk_id(ns, connector_uri, full_uri, "schema_summary", None, None),
                        "namespace_id": ns, "connector_uri": connector_uri, "object_uri": full_uri,
                        "locator": None, "lines": None, "content": summ[:65000], "dense_vec": vec,
                        "chunk_kind": "schema_summary", "metadata": {}, "indexed_at": int(time.time() * 1000)}
                    await asyncio.to_thread(self.milvus.delete_by_object, ns, connector_uri, full_uri)
                    await asyncio.to_thread(self.milvus.upsert, ns, [row])
                    chunk_count = 1
                    search_status = "indexed"

        elif okind == "directory" and self.summary.enabled != "false":
            # directory_summary chunk: an LLM description of a folder node from its listing
            entries = await plugin.list(relpath)
            listing = "\n".join(f"{e.type}\t{e.name}" for e in entries)
            if listing.strip():
                summ = await self.summary.summarize(listing, "directory_summary")
                if summ.strip():
                    vec = (await self.embed.batch_embed([summ]))[0]
                    row = {
                        "chunk_id": chunk_id(ns, connector_uri, full_uri, "directory_summary", None, None),
                        "namespace_id": ns, "connector_uri": connector_uri, "object_uri": full_uri,
                        "locator": None, "lines": None, "content": summ[:65000], "dense_vec": vec,
                        "chunk_kind": "directory_summary", "metadata": {}, "indexed_at": int(time.time() * 1000)}
                    await asyncio.to_thread(self.milvus.delete_by_object, ns, connector_uri, full_uri)
                    await asyncio.to_thread(self.milvus.upsert, ns, [row])
                    chunk_count = 1
                    search_status = "indexed"

        await self.meta.execute(
            "INSERT INTO objects (connector_id, object_uri, parent_path, type, media_type, size_hint, "
            " fingerprint, indexable, last_seen, search_status, chunk_count, indexed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(connector_id, object_uri) DO UPDATE SET "
            " type=excluded.type, media_type=excluded.media_type, size_hint=excluded.size_hint, "
            " fingerprint=excluded.fingerprint, indexable=excluded.indexable, last_seen=excluded.last_seen, "
            " search_status=excluded.search_status, chunk_count=excluded.chunk_count, indexed_at=excluded.indexed_at",
            (cid, relpath, os.path.dirname(relpath) or "/", st.type, st.media_type, st.size_hint,
             st.fingerprint, 1 if indexable else 0, _now(), search_status, chunk_count, _now()))

        await plugin.on_object_indexed(relpath)

    # --- search (design/06 §7) ---
    async def search(self, query: str, connector_uri: str | None = None,
                     object_prefix: str | None = None, mode: str = "hybrid", top_k: int = 10,
                     chunk_kinds: list[str] | None = None, collapse: bool = False) -> list[dict]:
        expr = build_filter(self.ns, connector_uri, object_prefix, chunk_kinds)
        if mode == "keyword":
            hits = await asyncio.to_thread(self.milvus.sparse_search, self.ns, query, top_k, expr)
        else:
            qvec = (await self.embed.batch_embed([query]))[0]
            if mode == "semantic":
                hits = await asyncio.to_thread(self.milvus.search_dense, self.ns, qvec, top_k, expr)
            else:  # hybrid
                hits = await asyncio.to_thread(
                    self.milvus.hybrid_search, self.ns, qvec, query, top_k, expr,
                    None, self.cfg.search.over_fetch_ratio)
        envs = [to_envelope(h) for h in hits]
        return collapse_by_object(envs) if collapse else envs

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

    # --- connector management (design/03 §3: probe / inspect / remove) ---
    async def probe(self, target: str, config: dict | None = None) -> dict:
        """Try-connect a connector without registering or writing state."""
        _, connector_uri, ctype, default_config = self._resolve_target(target)
        cfg_dict = config if config is not None else default_config
        plugin, _ = self._build_plugin(ctype, cfg_dict, "probe-" + uuid.uuid4().hex)
        try:
            await plugin.connect()
            hs = await plugin.healthcheck()
            return {"target": connector_uri, "type": ctype, "ok": hs.ok, "detail": hs.detail}
        except Exception as e:  # noqa: BLE001
            return {"target": connector_uri, "type": ctype, "ok": False, "detail": str(e)}
        finally:
            try:
                await plugin.close()
            except Exception:  # noqa: BLE001
                pass

    async def inspect(self, target: str) -> dict | None:
        """Connector row + object/job summary (design/03 §3 inspect)."""
        _, connector_uri, _, _ = self._resolve_target(target)
        row = await self.meta.fetchone(
            "SELECT id, root_uri, type, status, registered_at FROM connectors "
            "WHERE namespace_id=? AND root_uri=?", (self.ns, connector_uri))
        if not row:
            return None
        cid = row["id"]
        objs = await self.meta.fetchall(
            "SELECT search_status, count(*) AS n FROM objects WHERE connector_id=? GROUP BY search_status", (cid,))
        jobs = await self.meta.fetchall(
            "SELECT status, count(*) AS n FROM connector_jobs WHERE connector_id=? GROUP BY status", (cid,))
        total = await self.meta.fetchone(
            "SELECT count(*) AS n, sum(chunk_count) AS chunks FROM objects WHERE connector_id=?", (cid,))
        return {**dict(row),
                "objects": {o["search_status"]: o["n"] for o in objs},
                "object_count": total["n"] or 0, "chunk_count": total["chunks"] or 0,
                "jobs": {j["status"]: j["n"] for j in jobs}}

    async def remove_connector(self, target: str) -> bool:
        """Remove a connector and everything it owns: Milvus chunks, artifacts, and all
        metadata rows (objects / tasks / jobs / state / file_state) (design/03 §3 remove)."""
        _, connector_uri, _, _ = self._resolve_target(target)
        row = await self.meta.fetchone(
            "SELECT id FROM connectors WHERE namespace_id=? AND root_uri=?", (self.ns, connector_uri))
        if not row:
            return False
        cid = row["id"]
        await self.meta.execute(
            "UPDATE connectors SET status='removing' WHERE id=?", (cid,))
        # 1. Milvus chunks for this connector partition
        await asyncio.to_thread(self.milvus.delete_by_connector, self.ns, connector_uri)
        # 2. best-effort artifact bytes per object
        objs = await self.meta.fetchall("SELECT object_uri FROM objects WHERE connector_id=?", (cid,))
        for o in objs:
            await self._drop_artifacts(self.ns, connector_uri + o["object_uri"])
        # 3. metadata rows
        for tbl, col in (("object_tasks", "connector_id"), ("connector_jobs", "connector_id"),
                         ("objects", "connector_id"), ("connector_state", "connector_id"),
                         ("file_state", "connector_id")):
            await self.meta.execute(f"DELETE FROM {tbl} WHERE {col}=?", (cid,))
        await self.meta.execute("DELETE FROM connectors WHERE id=?", (cid,))
        return True

    # --- read commands (design/05) — any connector ---
    async def _match_connector(self, path: str) -> tuple[dict, str] | None:
        """Find the registered connector whose root is the longest prefix of `path`;
        return (connector_row, relpath) or None. Shared by _open_path (read commands)
        and resolve_connector_uri (search/grep scope). Handles local file paths (file
        connector) and scheme URIs (postgres://, github://, ...)."""
        rows = await self.meta.fetchall(
            "SELECT * FROM connectors WHERE namespace_id=?", (self.ns,))
        m = _SCHEME_RE.match(path)
        if m and m.group(1) != "file":
            best, best_root = None, ""
            for r in rows:
                ru = r["root_uri"]
                if "://" not in ru:
                    continue
                if path == ru or path.startswith(ru.rstrip("/") + "/"):
                    if len(ru) > len(best_root):
                        best, best_root = r, ru
            if best is None:
                return None
            rel = path[len(best_root):] or "/"
            if not rel.startswith("/"):
                rel = "/" + rel
            return best, rel
        # local file path -> file connector whose root is the longest prefix
        abs_path = os.path.abspath(path)
        best, best_root = None, ""
        for r in rows:
            if r["type"] != "file":
                continue
            root_abs = r["root_uri"].replace("file://local", "", 1)
            if abs_path == root_abs or abs_path.startswith(root_abs.rstrip("/") + "/"):
                if len(root_abs) > len(best_root):
                    best, best_root = r, root_abs
        if best is None:
            return None
        rel = "/" if abs_path == best_root else "/" + os.path.relpath(abs_path, best_root)
        return best, rel

    async def _open_path(self, path: str):
        """(connector_id, connector_uri, relpath, plugin) for the registered connector
        whose root is the longest prefix of `path`."""
        import json
        match = await self._match_connector(path)
        if match is None:
            raise ValueError(f"path not under any registered connector: {path}")
        row, rel = match
        plugin, _ = self._build_plugin(row["type"], json.loads(row["config_json"]), row["id"])
        await plugin.connect()
        return row["id"], row["root_uri"], rel, plugin

    async def ls(self, path: str) -> dict:
        """List children, each enriched with its full path + index state from the
        objects table, plus the connector's capabilities (design/03 §11 ls)."""
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
            row = await self.meta.fetchone(
                "SELECT search_status, indexable FROM objects WHERE connector_id=? AND object_uri=?",
                (cid, child_rel))
            out.append({
                "name": e.name, "type": e.type, "media_type": e.media_type, "size_hint": e.size_hint,
                "path": curi + child_rel,
                "search_status": row["search_status"] if row else None,
                "indexable": (bool(row["indexable"]) if row and row["indexable"] is not None else None),
            })
        return {"entries": out, "capabilities": caps}

    @staticmethod
    def _locator_matches(rec: dict, ocfg, idx: int, locator: dict) -> bool:
        if "_row" in locator:
            return idx == int(locator["_row"])
        keys = ocfg.locator_fields or list(locator.keys())
        return all(str(rec.get(k)) == str(locator.get(k)) for k in keys if k in locator)

    async def cat(self, path: str, range: tuple[int, int] | None = None, meta: bool = False,
                  density: str | None = None, locator: dict | None = None):
        import json as _json

        from ..connectors.base import Range
        _, curi, rel, plugin = await self._open_path(path)
        try:
            st = await plugin.stat(rel)
            if st.type == "dir":
                raise IsADirectoryError(path)
            if meta:
                return {"source": curi + rel, "media_type": st.media_type,
                        "size_hint": st.size_hint, "fingerprint": st.fingerprint}
            okind = plugin.object_kind_of(rel)
            structured = okind in ("table_rows", "record_collection", "message_stream")

            # --- locator: reopen a single structured record (design/05 §3, 06 §3) ---
            if locator is not None:
                records = plugin.read_records(rel)
                if records is None:
                    raise ValueError("range_unsupported")     # not a structured object
                ocfg = plugin.ctx.object_config_for(rel)
                i = 0
                async for rec in records:
                    if self._locator_matches(rec, ocfg, i, locator):
                        return {"source": curi + rel, "locator": locator,
                                "content": _json.dumps(rec, default=str, ensure_ascii=False)}
                    i += 1
                raise ValueError("locator_not_found")

            # --- structured object: head/range pushdown over records (lazy, not materialized) ---
            if structured:
                records = plugin.read_records(rel)
                if records is not None:
                    start = range[0] if range else 0
                    end = range[1] if range else start + 200      # default cap for a bare cat
                    out, i = [], 0
                    async for rec in records:
                        if i >= end:
                            break
                        if i >= start:
                            out.append(_json.dumps(rec, default=str, ensure_ascii=False))
                        i += 1
                    return "\n".join(out)

            ext = os.path.splitext(rel)[1].lower()
            text: str | None = None
            if ext in CONVERT_EXTS:        # pdf/docx/html etc. -> return converted markdown
                art = await self._read_artifact(self.ns, curi + rel, "converted_md")
                if art is not None:
                    text = art.decode("utf-8", errors="replace")
            if text is None:
                art_vlm = await self._read_artifact(self.ns, curi + rel, "vlm_text")
                if art_vlm is not None:    # image -> VLM description
                    return art_vlm.decode("utf-8", errors="replace")
            if text is None:
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

    async def head(self, path: str, n: int = 20) -> str:
        # fast path: pre-cached first rows of a structured object (design/05 head_cache)
        try:
            _, curi, rel, plugin = await self._open_path(path)
            try:
                if plugin.object_kind_of(rel) in ("table_rows", "record_collection", "message_stream"):
                    art = await self._read_artifact(self.ns, curi + rel, "head_cache")
                    if art is not None:
                        return "\n".join(art.decode("utf-8", errors="replace").splitlines()[:n])
            finally:
                await plugin.close()
        except Exception:  # noqa: BLE001 - fall back to cat below
            pass
        text = await self.cat(path)
        return "\n".join(text.splitlines()[:n])

    async def tail(self, path: str, n: int = 20) -> str:
        text = await self.cat(path)
        return "\n".join(text.splitlines()[-n:])

    async def grep(self, pattern: str, path: str, top_k: int = 100, regex: bool = False) -> list[dict]:
        """Dispatch: pushdown (file: none) -> BM25 (indexed scope) -> linear scan
        (not_indexed objects in scope). design/05 §6. The linear scan uses the native
        accelerator (mfs_server_rs) when the object is a real local file, else falls
        back to reading bytes + pure-Python regex."""
        from ..common import accel
        from ..connectors.base import GrepOptions
        cid, curi, rel, plugin = await self._open_path(path)
        scope_prefix = (curi + rel) if rel != "/" else None
        try:
            results: list[dict] = []
            # 2a connector grep pushdown (design/05 §6 step 1): exact, source-side (e.g.
            # SQL ILIKE for structured connectors). Returns None when unsupported.
            ocfg = plugin.ctx.object_config_for(rel)
            try:
                gen = await plugin.grep(pattern, rel, GrepOptions(
                    pattern=pattern, text_fields=ocfg.text_fields,
                    metadata_fields=ocfg.metadata_fields))
            except Exception:  # noqa: BLE001 - pushdown failure shouldn't kill grep
                gen = None
            if gen is not None:
                async for gm in gen:
                    results.append({"source": curi + gm.path, "lines": [gm.line_no, gm.line_no]
                                    if gm.line_no else None, "locator": gm.locator,
                                    "content": gm.content, "via": "pushdown"})
                return results
            # 2b BM25 over indexed objects in scope
            expr = build_filter(self.ns, curi, scope_prefix)
            hits = await asyncio.to_thread(self.milvus.sparse_search, self.ns, pattern, top_k, expr)
            for h in hits:
                e = h.get("entity", h)
                results.append({"source": e.get("object_uri"), "lines": e.get("lines"),
                                "content": e.get("content"), "via": "bm25"})
            # 2c linear scan over not_indexed objects in scope (file connector)
            root_abs = curi.replace("file://local", "", 1) if curi.startswith("file://local") else None
            like = (rel.rstrip("/") + "%") if rel != "/" else "%"
            not_idx = await self.meta.fetchall(
                "SELECT object_uri FROM objects WHERE connector_id=? AND search_status='not_indexed' "
                "AND object_uri LIKE ?", (cid, like))
            for o in not_idx[:50]:
                relp = o["object_uri"]
                try:
                    abs_file = (root_abs + relp) if root_abs else None
                    if abs_file and os.path.isfile(abs_file):
                        # native (or pure-Python) streaming grep straight off disk
                        for ln, line in await asyncio.to_thread(
                                accel.linear_grep_file, abs_file, pattern,
                                False, regex, 200):
                            results.append({"source": curi + relp, "lines": [ln, ln],
                                            "content": line, "via": "linear"})
                    else:
                        rx = re.compile(pattern if regex else re.escape(pattern))
                        buf = bytearray()
                        async for ch in plugin.read(relp):
                            buf += ch
                        text = bytes(buf).decode("utf-8", errors="replace")
                        for i, line in enumerate(text.splitlines(), 1):
                            if rx.search(line):
                                results.append({"source": curi + relp, "lines": [i, i],
                                                "content": line, "via": "linear"})
                except Exception:  # noqa: BLE001
                    pass
            return results
        finally:
            await plugin.close()
