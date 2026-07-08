"""PipelineSupervisor unit tests (engine-redesign 4b §7.1).

Covers the pieces Engine used to own and now delegates to PipelineSupervisor: lazy
construction, startup ordering, the ArtifactStoreAdapter wiring (bypassing Engine's
thin delegates), the routes_to_pipeline table, the _pending_finalize lifecycle
(stash_finalize write site + the two pop sites: _on_object_indexed and pump's
produce-error path), the recover-with-factory path, and shutdown ordering.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from mfs_server.config import ServerConfig
from mfs_server.connectors.base import PathStat
from mfs_server.engine.components.connector_factory import BuiltPlugin
from mfs_server.engine.engine import Engine
from mfs_server.engine.pipeline_supervisor import PipelineSupervisor
import mfs_server.engine.pipeline_supervisor as ps_mod


class _FakeEmbed:
    provider_name = "fake"
    model = "fake-model"
    version = "1"

    def _key(self, text):
        return "k:" + text

    async def _embed_api(self, texts):
        return [[0.1] for _ in texts]


class _RecordingMilvus:
    def __init__(self):
        self.deletes: list[tuple[str, str, str]] = []
        self.upserts: list = []

    def upsert(self, ns, rows):
        self.upserts.append((ns, rows))

    def delete_by_object(self, ns, connector_uri, object_uri):
        self.deletes.append((ns, connector_uri, object_uri))


class _FakeTxCache:
    async def batch_get(self, keys):
        return {k: None for k in keys}

    async def batch_put(self, entries):
        return None


class _RecordingPlugin:
    """Captures on_object_indexed so a test can assert cursor advances."""

    def __init__(self):
        self.indexed: list[str] = []

    async def on_object_indexed(self, rel):
        self.indexed.append(rel)


class _FakePlugin:
    """Stand-in for a connected plugin in the pump error test (provides .ctx)."""

    class ctx:
        @staticmethod
        def object_config_for(relpath):
            return None


class _FakeRecoverPlugin:
    async def connect(self):
        pass

    def object_kind_of(self, relpath):
        return "document"


async def _build_engine(tmp_path) -> Engine:
    cfg = ServerConfig()
    cfg.metadata.backend = "sqlite"
    cfg.metadata.path = str(tmp_path / "meta.db")
    cfg.transformation_cache.backend = "sqlite"
    cfg.transformation_cache.db_path = str(tmp_path / "tx.db")
    cfg.artifact_cache.root = str(tmp_path / "art")
    eng = Engine(cfg)
    eng.infra.embed = _FakeEmbed()
    eng.infra.milvus = _RecordingMilvus()
    eng.infra.tx_cache = _FakeTxCache()
    await eng.infra.meta.connect()
    await eng.infra.meta.init_schema()
    # seed object_tasks without parent connector/job rows (like test_engine_cancel_reconcile):
    # several tests below insert a bare task row, so disable FK enforcement for the session.
    await eng.infra.meta.execute("PRAGMA foreign_keys=OFF")
    return eng


def _stat(rel: str) -> PathStat:
    return PathStat(
        path=rel,
        type="file",
        media_type="text/markdown",
        size_hint=10,
        fingerprint="fp:" + rel,
    )


async def _seed_task(eng, *, task_id, job_id, cid, object_uri, status):
    await eng.infra.meta.execute(
        "INSERT INTO object_tasks (id, connector_job_id, connector_id, object_uri, old_uri, "
        " change_kind, status, priority, attempts) VALUES (?,?,?,?,?,?,?,?,0)",
        (task_id, job_id, cid, object_uri, None, "added", status, 0),
    )


# 1. construction is lazy (singletons None / _pending_finalize empty)
async def test_construct_lazy(tmp_path):
    eng = await _build_engine(tmp_path)
    try:
        sup = eng.pipeline
        assert isinstance(sup, PipelineSupervisor)
        assert sup._chunks_q is None
        assert sup._embed_consumer is None
        assert sup._producer_ctx is None
        assert sup._job_lane is None
        assert sup._job_watcher is None
        assert sup._job_watcher_task is None
        assert sup._pending_finalize == {}
    finally:
        await eng.infra.meta.close()


# 9. Engine integration smoke: read-only properties forward; supervisor holds Engine's deps
async def test_engine_forwards_to_supervisor(tmp_path):
    eng = await _build_engine(tmp_path)
    try:
        assert eng.pipeline is not None
        assert eng._embed_consumer is eng.pipeline._embed_consumer
        assert eng._job_lane is eng.pipeline._job_lane
        assert eng._producer_ctx is eng.pipeline._producer_ctx
        assert eng.pipeline._obj is eng.objects
        assert eng.pipeline._art is eng.artifacts
        assert eng.pipeline._factory is eng.connector_factory
        assert eng.pipeline._infra is eng.infra
        assert eng.pipeline._ns == eng.ns
    finally:
        await eng.infra.meta.close()


# 4. routes_to_pipeline table
async def test_routes_to_pipeline_table(tmp_path):
    eng = await _build_engine(tmp_path)
    try:
        sup = eng.pipeline
        for okind in (
            "document",
            "code",
            "text_blob",
            "message_stream",
            "record_collection",
            "table_rows",
        ):
            assert sup.routes_to_pipeline(okind) is True
        # image / table_schema are conditional on their feature flags. image reads
        # cfg.description.enabled; table_schema reads infra.summary.enabled (cached on the
        # CachingSummaryClient at construction, so flip the client attribute, not the cfg).
        eng.cfg.description.enabled = False
        eng.infra.summary.enabled = False
        assert sup.routes_to_pipeline("image") is False
        assert sup.routes_to_pipeline("table_schema") is False
        eng.cfg.description.enabled = True
        eng.infra.summary.enabled = True
        assert sup.routes_to_pipeline("image") is True
        assert sup.routes_to_pipeline("table_schema") is True
        # everything else is metadata-only
        assert sup.routes_to_pipeline("binary") is False
        assert sup.routes_to_pipeline("dir_summary") is False
    finally:
        await eng.infra.meta.close()


# 3. ArtifactStoreAdapter is wired with ArtifactCacheService methods (bypassing Engine delegates)
async def test_build_pipeline_wires_artifact_adapter_to_cache_service(tmp_path):
    eng = await _build_engine(tmp_path)
    eng.pipeline._build_pipeline()
    try:
        adapter = eng.pipeline._producer_ctx.artifacts
        # the adapter's callables are bound to ArtifactCacheService (not Engine's thin
        # _put_artifact / _read_artifact delegates) - §2.7. Bound methods are a fresh
        # object per attribute access, so compare the bound self, not identity.
        assert adapter._put.__self__ is eng.artifacts
        assert adapter._read.__self__ is eng.artifacts
        assert adapter._read_fresh.__self__ is eng.artifacts
    finally:
        await eng._job_lane.stop()
        await eng._embed_consumer.shutdown()
        await eng.infra.meta.close()


# 5a. stash_finalize + _on_object_indexed success pop
async def test_stash_finalize_and_success_pop(tmp_path):
    eng = await _build_engine(tmp_path)
    cid, job_id, connector_uri = "cA", "job1", "file:///repo"
    relpath, task_id = "/a.md", uuid.uuid4().hex
    task_uri = connector_uri + relpath
    await _seed_task(
        eng, task_id=task_id, job_id=job_id, cid=cid, object_uri=relpath, status="running"
    )
    plugin = _RecordingPlugin()
    try:
        eng.pipeline.stash_finalize(
            task_uri, (cid, connector_uri, relpath, _stat(relpath), True, plugin, task_id)
        )
        assert task_uri in eng.pipeline._pending_finalize  # stashed
        await eng.pipeline._on_object_indexed(task_uri, job_id, chunk_count=3, partial=False)
        assert task_uri not in eng.pipeline._pending_finalize  # pop #1
        assert plugin.indexed == [relpath]  # cursor advanced
        assert eng.infra.milvus.deletes == []  # no reconcile delete on success
    finally:
        await eng.infra.meta.close()


# 5b. _on_object_indexed error branch writes failed + leaves cursor
async def test_on_object_indexed_error_branch_writes_failed(tmp_path):
    eng = await _build_engine(tmp_path)
    cid, job_id, connector_uri = "cB", "job2", "file:///repo"
    relpath, task_id = "/b.md", uuid.uuid4().hex
    task_uri = connector_uri + relpath
    await _seed_task(
        eng, task_id=task_id, job_id=job_id, cid=cid, object_uri=relpath, status="running"
    )
    plugin = _RecordingPlugin()
    try:
        eng.pipeline.stash_finalize(
            task_uri, (cid, connector_uri, relpath, _stat(relpath), True, plugin, task_id)
        )
        await eng.pipeline._on_object_indexed(
            task_uri, job_id, chunk_count=0, partial=False, error="RuntimeError: boom"
        )
        assert task_uri not in eng.pipeline._pending_finalize  # popped on error too
        assert plugin.indexed == []  # cursor NOT advanced
        row = await eng.infra.meta.fetchone(
            "SELECT search_status FROM objects WHERE connector_id=? AND object_uri=?",
            (cid, relpath),
        )
        assert row["search_status"] == "failed"
        t = await eng.infra.meta.fetchone("SELECT status FROM object_tasks WHERE id=?", (task_id,))
        assert t["status"] == "failed"
    finally:
        await eng.infra.meta.close()


# 5c. _on_object_indexed won==0 (cancelled race) purges orphan chunks
async def test_on_object_indexed_won_zero_purges_orphan(tmp_path):
    eng = await _build_engine(tmp_path)
    cid, job_id, connector_uri = "cC", "job3", "file:///repo"
    relpath, task_id = "/c.md", uuid.uuid4().hex
    task_uri = connector_uri + relpath
    await _seed_task(
        eng, task_id=task_id, job_id=job_id, cid=cid, object_uri=relpath, status="cancelled"
    )
    plugin = _RecordingPlugin()
    try:
        eng.pipeline.stash_finalize(
            task_uri, (cid, connector_uri, relpath, _stat(relpath), True, plugin, task_id)
        )
        await eng.pipeline._on_object_indexed(task_uri, job_id, chunk_count=3, partial=False)
        assert task_uri not in eng.pipeline._pending_finalize  # popped
        assert eng.infra.milvus.deletes == [(eng.ns, connector_uri, task_uri)]  # orphan purged
        assert plugin.indexed == []  # no cursor advance
        row = await eng.infra.meta.fetchone(
            "SELECT * FROM objects WHERE connector_id=? AND object_uri=?", (cid, relpath)
        )
        assert row is None  # no objects row committed
    finally:
        await eng.infra.meta.close()


# 6. pump drops the stashed finalize context when produce() raises (pop #2)
async def test_pump_pops_pending_finalize_on_produce_error(tmp_path):
    eng = await _build_engine(tmp_path)
    eng.pipeline._build_pipeline()
    full_uri = "file:///repo/x.md"
    try:
        eng.pipeline.stash_finalize(
            full_uri, ("cid", "file:///repo", "/x.md", None, True, _FakePlugin(), "tid")
        )
        assert full_uri in eng.pipeline._pending_finalize

        class _BoomProducer:
            async def produce(self, task):
                raise RuntimeError("produce failed")
                yield  # pragma: no cover - makes produce an async generator

        original = ps_mod.select_producer
        ps_mod.select_producer = lambda okind, ctx: _BoomProducer()
        try:
            with pytest.raises(RuntimeError, match="produce failed"):
                await eng.pipeline.pump(
                    plugin=_FakePlugin(),
                    connector_uri="file:///repo",
                    relpath="/x.md",
                    full_uri=full_uri,
                    okind="document",
                    task={"id": "tid", "change_kind": "added", "connector_job_id": "j1"},
                )
        finally:
            ps_mod.select_producer = original
        assert full_uri not in eng.pipeline._pending_finalize  # pop #2
    finally:
        await eng._job_lane.stop()
        await eng._embed_consumer.shutdown()
        await eng.infra.meta.close()


# 7. _recover_job_lane rebuilds via factory.build_plugin(...).plugin (D2 unpack)
async def test_recover_job_lane_uses_factory_built_plugin(tmp_path):
    eng = await _build_engine(tmp_path)
    eng.cfg.summary.enabled = True  # so _job_lane.enabled is True
    eng.pipeline._build_pipeline()
    fake_plugin = _FakeRecoverPlugin()
    captured: dict = {}

    async def _list_running_jobs():
        return [{"id": "j1", "connector_id": "c1"}]

    async def _get_connector_root_type_config(cid):
        return {"root_uri": "file:///repo", "type": "file", "config_json": "{}"}

    async def _list_job_tasks_excluding_dir_summary(job_id):
        return [{"object_uri": "/a.md", "status": "running"}]

    def _build_plugin(ctype, config, cid):
        return BuiltPlugin(plugin=fake_plugin, ctx=None)

    def _recover_job(job_id, connector_uri, plugin, objects, existing):
        captured["plugin"] = plugin

    eng.pipeline._obj.list_running_jobs = _list_running_jobs
    eng.pipeline._obj.get_connector_root_type_config = _get_connector_root_type_config
    eng.pipeline._obj.list_job_tasks_excluding_dir_summary = _list_job_tasks_excluding_dir_summary
    eng.pipeline._factory.build_plugin = _build_plugin
    eng.pipeline._job_lane.recover_job = _recover_job
    try:
        await eng.pipeline._recover_job_lane()
        assert captured.get("plugin") is fake_plugin  # D2: factory.build_plugin(...).plugin
    finally:
        await eng._job_lane.stop()
        await eng._embed_consumer.shutdown()
        await eng.infra.meta.close()


# 2. startup runs build -> gc -> recover -> watcher (in that order)
async def test_startup_order(tmp_path):
    eng = await _build_engine(tmp_path)
    calls: list[str] = []
    eng.pipeline._build_pipeline = lambda: calls.append("build")

    async def _gc():
        calls.append("gc")
        return 0

    async def _recover():
        calls.append("recover")

    eng.pipeline._gc_orphan_chunks = _gc
    eng.pipeline._recover_job_lane = _recover

    class _FakeWatcher:
        def __init__(self, meta, lane):
            calls.append("watcher_init")

        def stop(self):
            pass

        async def run(self):
            calls.append("watcher_run")
            await asyncio.sleep(0)

    orig_watcher = ps_mod.ConnectorJobWatcher
    ps_mod.ConnectorJobWatcher = _FakeWatcher
    try:
        await eng.pipeline.startup()
        assert calls[:3] == ["build", "gc", "recover"]
        assert "watcher_init" in calls
        assert eng.pipeline._job_watcher_task is not None
    finally:
        ps_mod.ConnectorJobWatcher = orig_watcher
        if eng.pipeline._job_watcher_task is not None:
            eng.pipeline._job_watcher_task.cancel()
            try:
                await eng.pipeline._job_watcher_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        await eng.infra.meta.close()


# 8. shutdown stops watcher -> lane -> embed_consumer (and nulls embed_consumer)
async def test_shutdown_order(tmp_path):
    eng = await _build_engine(tmp_path)
    calls: list[str] = []

    class _FakeWatcher:
        def stop(self):
            calls.append("watcher_stop")

    async def _watcher_run():
        calls.append("watcher_run")

    class _FakeLane:
        async def stop(self):
            calls.append("lane_stop")

    class _FakeEC:
        async def shutdown(self):
            calls.append("ec_shutdown")

    eng.pipeline._job_watcher = _FakeWatcher()
    eng.pipeline._job_watcher_task = asyncio.create_task(_watcher_run())
    eng.pipeline._job_lane = _FakeLane()
    eng.pipeline._embed_consumer = _FakeEC()
    try:
        await eng.pipeline.shutdown()
        assert calls == ["watcher_stop", "watcher_run", "lane_stop", "ec_shutdown"]
        assert eng.pipeline._embed_consumer is None  # nulled after shutdown
    finally:
        await eng.infra.meta.close()
