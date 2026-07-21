"""ConnectorManager: probe, estimate, inspect, and remove connectors.

Provides connector lifecycle management operations: pre-flight health checks
(probe), cost estimation (estimate), metadata inspection (inspect), and
full removal including Milvus chunks, artifacts, and metadata cleanup.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

from ..config import ServerConfig
from ..connectors.base import SyncOptions
from .components import ConnectorLocator
from .components.artifact_cache import ArtifactCacheService
from .components.connector_factory import ConnectorFactory
from .components.object_repository import ObjectRepository
from .producers.render import render_record
from .worker import _JOB_STALE_AFTER_S

logger = logging.getLogger(__name__)


class ConnectorManager:
    """Connector lifecycle management: probe, estimate, inspect, remove.

    Provides pre-flight health checks, cost estimation, metadata inspection,
    and full connector removal (Milvus chunks, artifacts, metadata cleanup).
    """

    def __init__(
        self,
        cfg: ServerConfig,
        infra,
        factory: ConnectorFactory,
        objects: ObjectRepository,
        artifacts: ArtifactCacheService,
        pipeline=None,
    ) -> None:
        self._cfg = cfg
        self._infra = infra
        self._factory = factory
        self._obj = objects
        self._art = artifacts
        self._pipeline = pipeline
        self._ns = cfg.namespace

    def _resolve_target(self, target: str) -> tuple[str, str, str, dict]:
        r = self._factory.resolve_target(target)
        return r.ctype, r.connector_uri, r.scheme, r.config

    def _build_plugin(self, ctype: str, config: dict, connector_id: str):
        built = self._factory.build_plugin(ctype, config, connector_id)
        return built.plugin, built.ctx

    async def _match_connector(self, path: str) -> tuple[dict, str] | None:
        rows = await self._obj.list_connectors_all()
        return ConnectorLocator.match(rows, path)

    async def _read_text(self, plugin, relpath: str) -> str:
        return (await self._read_bytes(plugin, relpath)).decode("utf-8", errors="replace")

    async def _read_bytes(self, plugin, relpath: str) -> bytes:
        buf = bytearray()
        async for chunk in plugin.read(relpath):
            buf += chunk
        return bytes(buf)

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
            row = await self._obj.get_connector_id_and_config_by_uri(connector_uri)
            cfg_dict = (
                json.loads(row["config_json"]) if row and row["config_json"] else default_config
            )
        self._factory.validate_config(ctype, cfg_dict)
        return cfg_dict

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
            plugin, _ = self._factory.build_plugin(ctype, cfg_dict, "probe-" + uuid.uuid4().hex)
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
        plugin, _ = self._factory.build_plugin(ctype, cfg_dict, tmp_cid)
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
                        t for t, _ in chunk_body(text, okind, ext, self._cfg.chunking.chunk_size)
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
                    await self._infra.meta.execute(
                        f"DELETE FROM {tbl} WHERE connector_id=?", (tmp_cid,)
                    )
                except Exception:  # noqa: BLE001
                    pass

    async def inspect(self, target: str) -> dict | None:
        """Connector row + object/job summary."""
        match = await self._match_connector(target)
        if match is not None:
            matched, _ = match
            row = await self._obj.get_connector_row(matched["id"])
        else:
            _, connector_uri, _, _ = self._resolve_target(target)
            row = await self._obj.get_connector_row_by_uri(connector_uri)
        if not row:
            return None
        cid = row["id"]
        objs = await self._obj.summarize_objects_by_search_status(cid)
        jobs = await self._obj.summarize_jobs_by_status(cid)
        total = await self._obj.summarize_objects_totals(cid)
        return {
            **dict(row),
            "objects": {o["search_status"]: o["n"] for o in objs},
            "object_count": total["n"] or 0,
            "chunk_count": total["chunks"] or 0,
            "jobs": {j["status"]: j["n"] for j in jobs},
        }

    async def _await_worker_drained(self, cid: str) -> None:
        """Wait for the worker to leave 'running' - that transition (set in finalize_job
        after run_job's loop exits) is the proof the last object's Milvus upsert has
        completed, so it's the only safe moment to delete. Don't bound by wall clock
        (the old ~10s cap would delete out from under an object still mid-write,
        re-opening the orphan-chunk race); trust the heartbeat. A live worker refreshes
        it per task, so keep waiting; only a stale heartbeat means the worker died/stuck,
        in which case WE take the job over and then delete."""
        stale_after_s = _JOB_STALE_AFTER_S
        while True:
            running = await self._obj.get_running_job_heartbeat(cid)
            if not running:
                break
            cutoff = (datetime.now(timezone.utc) - timedelta(seconds=stale_after_s)).isoformat()
            if not running["heartbeat"] or running["heartbeat"] < cutoff:
                # worker is dead or wedged - reclaim: cancel its in-flight tasks + the job
                # so the 'running' row clears and no later write can resurrect it.
                await self._obj.cancel_pending_running_tasks_for_job(running["id"])
                await self._obj.cancel_running_job(running["id"])
                break
            await asyncio.sleep(0.1)

    async def remove_connector(self, target: str) -> bool:
        """Remove a connector and everything it owns: Milvus chunks, artifacts, and all
        metadata rows (objects / tasks / jobs / state / file_state)."""
        _, connector_uri, _, _ = self._resolve_target(target)
        cid = await self._obj.get_connector_id_by_uri(connector_uri)
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
        await self._obj.set_connector_removing(cid)
        await self._obj.cancel_pending_tasks_for_connector(cid)
        await self._obj.cancel_queued_preparing_jobs(cid)
        await self._await_worker_drained(cid)
        # 1. Milvus chunks for this connector partition (worker has now stopped writing)
        await asyncio.to_thread(self._infra.milvus.delete_by_connector, self._ns, connector_uri)
        # 2. best-effort artifact bytes per object
        objs = await self._obj.list_object_uris_for_connector(cid)
        for o in objs:
            await self._art.drop_artifacts(self._ns, connector_uri + o["object_uri"])
        # 3. metadata rows — the three target tables via the repo; connector_state / file_state
        # are out of this repo's four-table scope and stay here.
        await self._obj.delete_object_task_job_rows_for_connector(cid)
        for tbl, col in (
            ("connector_state", "connector_id"),
            ("file_state", "connector_id"),
        ):
            await self._infra.meta.execute(f"DELETE FROM {tbl} WHERE {col}=?", (cid,))
        await self._obj.delete_connector(cid)
        return True
