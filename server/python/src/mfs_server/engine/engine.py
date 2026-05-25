"""Engine: orchestration for `mfs add` (register connector -> job -> sync ->
object_tasks -> worker). Phase 2 worker is a stub that writes objects/file_state and
marks tasks succeeded (no chunk/embed/Milvus yet); _index_object is the seam Phase 3
fills with real chunk/embed/Milvus-upsert.

per-object atomic + job inheritance (design/02 §6.4 §7.1) are honored in structure.
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone

from ..config import ServerConfig
from ..connectors.base import ConnectorContext, ObjectConfig, SyncOptions
from ..connectors.registry import get_plugin_cls, load_builtin
from ..storage.file_state import FileStateStore
from ..storage.metadata import MetadataStore
from ..storage.milvus import MilvusStore
from ..storage.object_store import LocalObjectStore
from ..storage.transformation_cache import TransformationCache
from .state import ConnectorStateStore

_SCHEME_RE = re.compile(r"^([a-z][a-z0-9+.\-]*)://")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Engine:
    def __init__(self, cfg: ServerConfig):
        self.cfg = cfg
        self.ns = cfg.namespace
        self.meta = MetadataStore(cfg)
        self.milvus = MilvusStore(cfg)
        self.object_store = LocalObjectStore(cfg)
        self.tx_cache = TransformationCache(cfg)

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
        if m and m.group(1) != "file":
            raise NotImplementedError(f"connector scheme '{m.group(1)}' not yet implemented")
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
        ctx = ConnectorContext(state, connector_id, self.ns,
                               object_config_resolver=lambda p: ObjectConfig())
        if ctype == "file":
            from ..connectors.file.plugin import FileConfig
            plugin = cls(FileConfig(root=config["root"], client_id=config.get("client_id", "local")),
                         None, ctx=ctx)
            plugin.file_state = FileStateStore(self.meta, self.ns, connector_id)
        else:
            plugin = cls(config, None, ctx=ctx)
        return plugin, ctx

    # --- add (register + sync + worker) ---
    async def add(self, target: str, full: bool = False, since: str | None = None) -> str:
        _, connector_uri, ctype, config = self._resolve_target(target)
        cid = await self.register_or_get_connector(connector_uri, ctype, config)

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

        plugin, ctx = self._build_plugin(ctype, config, cid)
        await plugin.connect()
        try:
            opts = SyncOptions(full=full, since=since)
            async for ch in plugin.sync(opts):
                tid = uuid.uuid4().hex
                await self.meta.execute(
                    "INSERT INTO object_tasks (id, connector_job_id, connector_id, object_uri, old_uri, "
                    " change_kind, status, priority, attempts) VALUES (?,?,?,?,?,?,?,?,0)",
                    (tid, job_id, cid, ch.uri, ch.old_uri, ch.kind, "pending", plugin.task_priority(ch)))

            await self._run_job(job_id, cid, connector_uri, plugin)
            await ctx.state.commit()
        finally:
            await plugin.close()

        # finalize job counts
        counts = await self.meta.fetchall(
            "SELECT status, count(*) AS n FROM object_tasks WHERE connector_job_id=? GROUP BY status", (job_id,))
        cmap = {r["status"]: r["n"] for r in counts}
        await self.meta.execute(
            "UPDATE connector_jobs SET status='succeeded', finished_at=?, "
            " total_objects=?, succeeded_objects=?, failed_objects=? WHERE id=?",
            (_now(), sum(cmap.values()), cmap.get("succeeded", 0), cmap.get("failed", 0), job_id))
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

    async def _run_job(self, job_id: str, cid: str, connector_uri: str, plugin) -> None:
        while True:
            tasks = await self._claim_batch(job_id, limit=64)
            if not tasks:
                break
            # Phase 3 will chunk all + batch-embed across tasks here; upsert+mark stays per-task.
            for t in tasks:
                try:
                    await self._index_object(plugin, connector_uri, t)
                    await self.meta.execute(
                        "UPDATE object_tasks SET status='succeeded', finished_at=? WHERE id=?",
                        (_now(), t["id"]))
                except Exception as e:  # noqa: BLE001
                    await self.meta.execute(
                        "UPDATE object_tasks SET status='failed', finished_at=?, last_error=? WHERE id=?",
                        (_now(), str(e), t["id"]))

    async def _index_object(self, plugin, connector_uri: str, task: dict) -> None:
        """Phase 2 stub: write objects/file_state. Phase 3 fills chunk/embed/Milvus."""
        relpath = task["object_uri"]
        kind = task["change_kind"]
        cid = task["connector_id"]
        if kind == "deleted":
            await self.meta.execute(
                "DELETE FROM objects WHERE connector_id=? AND object_uri=?", (cid, relpath))
            # Phase 3: self.milvus.delete_by_object(ns, connector_uri, connector_uri+relpath)
            await plugin.on_object_deleted(relpath)
            return

        st = await plugin.stat(relpath)
        okind = plugin.object_kind_of(relpath)
        indexable = okind not in ("binary",)
        chunk_count = 0   # Phase 3: real chunk/embed -> count
        await self.meta.execute(
            "INSERT INTO objects (connector_id, object_uri, parent_path, type, media_type, size_hint, "
            " fingerprint, indexable, last_seen, search_status, chunk_count, indexed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(connector_id, object_uri) DO UPDATE SET "
            " type=excluded.type, media_type=excluded.media_type, size_hint=excluded.size_hint, "
            " fingerprint=excluded.fingerprint, indexable=excluded.indexable, last_seen=excluded.last_seen, "
            " search_status=excluded.search_status, chunk_count=excluded.chunk_count, indexed_at=excluded.indexed_at",
            (cid, relpath, os.path.dirname(relpath) or "/", st.type, st.media_type, st.size_hint,
             st.fingerprint, 1 if indexable else 0, _now(),
             "indexed" if indexable else "not_indexed", chunk_count, _now()))

        if kind == "renamed" and task["old_uri"]:
            await self.meta.execute(
                "DELETE FROM objects WHERE connector_id=? AND object_uri=?", (cid, task["old_uri"]))
            # Phase 3: Milvus chunk_id rewrite old_uri -> relpath (reuse vectors)

        await plugin.on_object_indexed(relpath)
