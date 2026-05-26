"""Engine: orchestration for `mfs add` (register connector -> job -> sync ->
object_tasks -> worker). Phase 2 worker is a stub that writes objects/file_state and
marks tasks succeeded (no chunk/embed/Milvus yet); _index_object is the seam Phase 3
fills with real chunk/embed/Milvus-upsert.

per-object atomic + job inheritance (design/02 §6.4 §7.1) are honored in structure.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
import uuid
from datetime import datetime, timezone

from ..common.converter import CONVERT_EXTS, CachingConverterClient
from ..common.embedding import CachingEmbeddingClient
from ..common.retrieval import build_filter, collapse_by_object, to_envelope
from ..common.vlm import CachingVlmClient
from ..config import ServerConfig
from ..connectors.base import ConnectorContext, ObjectConfig, SyncOptions
from ..connectors.registry import get_plugin_cls, load_builtin
from ..processors.text import chunk_body
from ..storage.file_state import FileStateStore
from ..storage.ids import chunk_id
from ..storage.metadata import MetadataStore
from ..storage.milvus import MilvusStore
from ..storage.object_store import LocalObjectStore
from ..storage.transformation_cache import TransformationCache
from .state import ConnectorStateStore

_SCHEME_RE = re.compile(r"^([a-z][a-z0-9+.\-]*)://")


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


def _render_record(rec: dict, text_fields: list[str], template: str | None = None) -> str:
    """Join configured text_fields into chunk content (design/06 §4 default template)."""
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
        self.object_store = LocalObjectStore(cfg)
        self.tx_cache = TransformationCache(cfg)
        self.embed = CachingEmbeddingClient(cfg, self.tx_cache)
        self.converter = CachingConverterClient(cfg, self.tx_cache)
        self.vlm = CachingVlmClient(cfg, self.tx_cache)

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

    async def register_or_get_connector(self, connector_uri: str, ctype: str, config: dict) -> str:
        import json
        row = await self.meta.fetchone(
            "SELECT id FROM connectors WHERE namespace_id=? AND root_uri=?", (self.ns, connector_uri))
        if row:
            return row["id"]
        cid = uuid.uuid4().hex
        await self.meta.execute(
            "INSERT INTO connectors (id, namespace_id, root_uri, type, status, config_json, registered_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (cid, self.ns, connector_uri, ctype, "active", json.dumps(config), _now()))
        return cid

    def _build_plugin(self, ctype: str, config: dict, connector_id: str):
        cls = get_plugin_cls(ctype)
        if cls is None:
            raise NotImplementedError(f"no plugin for {ctype}")
        state = ConnectorStateStore(self.meta, connector_id)
        objects_cfg = config.get("objects", []) if isinstance(config, dict) else []
        ctx = ConnectorContext(state, connector_id, self.ns,
                               object_config_resolver=lambda p: _match_object_config(objects_cfg, p))
        if ctype == "file":
            from ..connectors.file.plugin import FileConfig
            plugin = cls(FileConfig(root=config["root"], client_id=config.get("client_id", "local")),
                         None, ctx=ctx)
            plugin.file_state = FileStateStore(self.meta, self.ns, connector_id)
        else:
            plugin = cls(config, None, ctx=ctx)
        return plugin, ctx

    # --- add (register + sync + worker) ---
    async def add(self, target: str, config: dict | None = None, full: bool = False,
                  since: str | None = None) -> str:
        import json
        _, connector_uri, ctype, default_config = self._resolve_target(target)
        cfg_dict = config if config is not None else default_config
        cid = await self.register_or_get_connector(connector_uri, ctype, cfg_dict)
        row0 = await self.meta.fetchone("SELECT config_json FROM connectors WHERE id=?", (cid,))
        stored_cfg = json.loads(row0["config_json"]) if row0 and row0["config_json"] else cfg_dict

        job_id = uuid.uuid4().hex
        await self.meta.execute(
            "INSERT INTO connector_jobs (id, namespace_id, connector_id, op_kind, trigger, status, "
            " started_at, heartbeat) VALUES (?,?,?,?,?,?,?,?)",
            (job_id, self.ns, cid, "sync", "manual", "running", _now(), _now()))

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
                tid = uuid.uuid4().hex
                await self.meta.execute(
                    "INSERT INTO object_tasks (id, connector_job_id, connector_id, object_uri, old_uri, "
                    " change_kind, status, priority, attempts) VALUES (?,?,?,?,?,?,?,?,0)",
                    (tid, job_id, cid, ch.uri, ch.old_uri, ch.kind, "pending", plugin.task_priority(ch)))

            aborted = await self._run_job(job_id, cid, connector_uri, plugin)
            await ctx.state.commit()
        finally:
            await plugin.close()

        # finalize job counts
        counts = await self.meta.fetchall(
            "SELECT status, count(*) AS n FROM object_tasks WHERE connector_job_id=? GROUP BY status", (job_id,))
        cmap = {r["status"]: r["n"] for r in counts}
        await self.meta.execute(
            "UPDATE connector_jobs SET status=?, finished_at=?, error=?, "
            " total_objects=?, succeeded_objects=?, failed_objects=?, cancelled_objects=? WHERE id=?",
            ("failed" if aborted else "succeeded", _now(), aborted,
             sum(cmap.values()), cmap.get("succeeded", 0), cmap.get("failed", 0),
             cmap.get("cancelled", 0), job_id))
        return job_id

    async def _claim_batch(self, job_id: str, limit: int) -> list[dict]:
        rows = await self.meta.fetchall(
            "SELECT * FROM object_tasks WHERE connector_job_id=? AND status='pending' "
            "ORDER BY priority ASC, started_at ASC LIMIT ?", (job_id, limit))
        for r in rows:
            await self.meta.execute(
                "UPDATE object_tasks SET status='running', started_at=?, attempts=attempts+1 WHERE id=?",
                (_now(), r["id"]))
        return rows

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
        indexable = okind not in ("binary",)

        if okind in ("document", "code"):
            ext = os.path.splitext(relpath)[1].lower()
            if okind == "document" and ext in CONVERT_EXTS:
                raw = await self._read_bytes(plugin, relpath)
                text = await self.converter.convert(raw, ext)
                await asyncio.to_thread(self.object_store.put_artifact, ns, full_uri,
                                        "converted_md", text.encode())
            else:
                text = await self._read_text(plugin, relpath)
                if connector_uri.startswith(("web://", "github://")):
                    await asyncio.to_thread(self.object_store.put_artifact, ns, full_uri,
                                            "converted_md", text.encode())
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
                await asyncio.to_thread(self.milvus.delete_by_object, ns, connector_uri, full_uri)
                await asyncio.to_thread(self.milvus.upsert, ns, rows)
                chunk_count = len(rows)
                search_status = "partial" if partial else "indexed"

        elif okind == "image":
            ext = os.path.splitext(relpath)[1].lower()
            raw = await self._read_bytes(plugin, relpath)
            desc = await self.vlm.describe(raw, ext)
            await asyncio.to_thread(self.object_store.put_artifact, ns, full_uri, "vlm_text", desc.encode())
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
                pairs: list[tuple[str, dict | None]] = []
                partial = False
                i = 0
                async for rec in records:
                    text = _render_record(rec, ocfg.text_fields, ocfg.text_template)
                    if text.strip():
                        loc = {f: rec.get(f) for f in ocfg.locator_fields} if ocfg.locator_fields else {"_row": i}
                        pairs.append((text, loc))
                    i += 1
                    if len(pairs) >= ocfg.chunk_max:
                        partial = True
                        break
                if pairs:
                    vecs = await self.embed.batch_embed([p[0] for p in pairs])
                    now_ms = int(time.time() * 1000)
                    rows = [{
                        "chunk_id": chunk_id(ns, connector_uri, full_uri, "row_text", loc, None),
                        "namespace_id": ns, "connector_uri": connector_uri, "object_uri": full_uri,
                        "locator": loc, "lines": None, "content": ctext[:65000], "dense_vec": vec,
                        "chunk_kind": "row_text", "metadata": {}, "indexed_at": now_ms,
                    } for (ctext, loc), vec in zip(pairs, vecs)]
                    await asyncio.to_thread(self.milvus.delete_by_object, ns, connector_uri, full_uri)
                    await asyncio.to_thread(self.milvus.upsert, ns, rows)
                    chunk_count = len(rows)
                    search_status = "partial" if partial else "indexed"

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

    def ctx_object_config(self, connector_id: str, relpath: str) -> ObjectConfig:
        # Phase 3: default config; connector TOML [[objects]] parsing comes later
        return ObjectConfig()

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

    def resolve_connector_uri(self, target: str) -> tuple[str, str | None]:
        """Map a user path/URI to (connector_uri, object_prefix) for search/grep scope."""
        _, connector_uri, _, _ = self._resolve_target(target)
        return connector_uri, None

    # --- read commands (design/05) — Phase 4 file connector ---
    async def _open_path(self, path: str):
        """(connector_id, connector_uri, relpath, plugin) for the registered file
        connector whose root is the longest prefix of the abs path."""
        import json
        abs_path = os.path.abspath(path)
        rows = await self.meta.fetchall(
            "SELECT * FROM connectors WHERE namespace_id=? AND type='file'", (self.ns,))
        best = None
        best_root = ""
        for r in rows:
            root_abs = r["root_uri"].replace("file://local", "", 1)
            if abs_path == root_abs or abs_path.startswith(root_abs.rstrip("/") + "/"):
                if len(root_abs) > len(best_root):
                    best, best_root = r, root_abs
        if best is None:
            raise ValueError(f"path not under any registered connector: {path}")
        rel = "/" if abs_path == best_root else "/" + os.path.relpath(abs_path, best_root)
        plugin, _ = self._build_plugin("file", json.loads(best["config_json"]), best["id"])
        await plugin.connect()
        return best["id"], best["root_uri"], rel, plugin

    async def ls(self, path: str) -> list[dict]:
        _, _, rel, plugin = await self._open_path(path)
        try:
            entries = await plugin.list(rel)
        finally:
            await plugin.close()
        return [{"name": e.name, "type": e.type, "media_type": e.media_type, "size_hint": e.size_hint}
                for e in entries]

    async def cat(self, path: str, range: tuple[int, int] | None = None, meta: bool = False):
        from ..connectors.base import Range
        _, curi, rel, plugin = await self._open_path(path)
        try:
            st = await plugin.stat(rel)
            if st.type == "dir":
                raise IsADirectoryError(path)
            if meta:
                return {"source": curi + rel, "media_type": st.media_type,
                        "size_hint": st.size_hint, "fingerprint": st.fingerprint}
            ext = os.path.splitext(rel)[1].lower()
            if ext in CONVERT_EXTS:        # pdf/docx/html etc. -> return converted markdown
                art = await asyncio.to_thread(self.object_store.get_artifact, self.ns,
                                              curi + rel, "converted_md")
                if art is not None:
                    return art.decode("utf-8", errors="replace")
            art_vlm = await asyncio.to_thread(self.object_store.get_artifact, self.ns,
                                              curi + rel, "vlm_text")
            if art_vlm is not None:        # image -> VLM description
                return art_vlm.decode("utf-8", errors="replace")
            rg = Range(range[0], range[1]) if range else None
            buf = bytearray()
            async for ch in plugin.read(rel, rg):
                buf += ch
            return bytes(buf).decode("utf-8", errors="replace")
        finally:
            await plugin.close()

    async def head(self, path: str, n: int = 20) -> str:
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
        cid, curi, rel, plugin = await self._open_path(path)
        scope_prefix = (curi + rel) if rel != "/" else None
        try:
            results: list[dict] = []
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
