"""IngestOrchestrator + ObjectIndexer unit tests (engine-redesign 4b).

Covers the NEW structure introduced by the extraction: the Engine->ingest wiring +
``bind_remover`` back-reference, and ``ObjectIndexer``'s four exits (deleted /
renamed-reuse / renamed-empty-fallthrough / pipeline 'deferred' / metadata-only).
The moved methods (``add`` / ``_drain_job`` / ``_run_job`` / ``_process_with_retry``
/ ...) are behavior-covered by the existing E2E suite (``test_engine_*.py``), which
now drives ``eng.ingest._run_job_loop`` etc.; this file adds structural unit cover for
the new dispatch + back-reference.
"""

from __future__ import annotations


from mfs_server.config import ServerConfig
from mfs_server.connectors.base import PathStat
from mfs_server.engine.engine import Engine
from mfs_server.engine.ingest import IngestOrchestrator, ObjectIndexer


# --- fakes ---


class _RecObjects:
    def __init__(self) -> None:
        self.deleted_rows: list[tuple[str, str]] = []
        self.written_rows: list[tuple] = []

    async def delete_object_row(self, cid, object_uri):
        self.deleted_rows.append((cid, object_uri))

    async def write_object_row(self, cid, relpath, st, indexable, search_status, chunk_count):
        self.written_rows.append((cid, relpath, st, indexable, search_status, chunk_count))


class _RecMilvus:
    def __init__(self, chunks_for: list | None = None) -> None:
        self.deletes: list[tuple[str, str, str]] = []
        self.upserts: list = []
        self._chunks_for = chunks_for

    def delete_by_object(self, ns, connector_uri, object_uri):
        self.deletes.append((ns, connector_uri, object_uri))

    def upsert(self, ns, rows):
        self.upserts.append((ns, rows))

    def get_chunks_by_object(self, ns, connector_uri, object_uri):
        return self._chunks_for if self._chunks_for is not None else []


class _RecArtifacts:
    def __init__(self) -> None:
        self.dropped: list[tuple[str, str]] = []
        self.renamed: list[tuple[str, str, str]] = []

    async def drop_artifacts(self, ns, object_uri):
        self.dropped.append((ns, object_uri))

    async def rename_artifacts(self, ns, old_uri, new_uri):
        self.renamed.append((ns, old_uri, new_uri))


class _FakeInfra:
    def __init__(self, milvus) -> None:
        self.milvus = milvus
        # _run_job reads self._infra.summary.enabled; not exercised by indexer tests


class _FakePipeline:
    def __init__(self, routes: bool = True) -> None:
        self._routes = routes
        self.stashed: list[tuple[str, tuple]] = []
        self.pumped: list[tuple] = []
        self.embed_consumer = None
        self.job_lane = None

    def routes_to_pipeline(self, okind):
        return self._routes

    def stash_finalize(self, full_uri, ctx):
        self.stashed.append((full_uri, ctx))

    async def pump(self, plugin, connector_uri, relpath, full_uri, okind, task):
        self.pumped.append((connector_uri, relpath, full_uri, okind))


class _FakeObjectConfig:
    def __init__(self, indexable: bool = True) -> None:
        self.indexable = indexable


class _PluginCtx:
    def __init__(self, ocfg) -> None:
        self._ocfg = ocfg

    def object_config_for(self, relpath):
        return self._ocfg


class _FakePlugin:
    def __init__(self, okind="document", indexable=True, stat=None) -> None:
        self._okind = okind
        self._stat = stat if stat is not None else _stat("/x")
        self.ctx = _PluginCtx(_FakeObjectConfig(indexable=indexable))
        self.indexed: list[str] = []
        self.deleted: list[str] = []

    async def stat(self, relpath):
        return self._stat

    def object_kind_of(self, relpath):
        return self._okind

    async def on_object_indexed(self, rel):
        self.indexed.append(rel)

    async def on_object_deleted(self, rel):
        self.deleted.append(rel)


def _stat(rel: str) -> PathStat:
    return PathStat(
        path=rel,
        type="file",
        media_type="text/markdown",
        size_hint=10,
        fingerprint="fp:" + rel,
    )


def _task(change_kind, object_uri="/a.md", old_uri=None, cid="c1", tid="t1") -> dict:
    return {
        "id": tid,
        "connector_id": cid,
        "object_uri": object_uri,
        "old_uri": old_uri,
        "change_kind": change_kind,
    }


async def _build_engine(tmp_path) -> Engine:
    cfg = ServerConfig()
    cfg.metadata.backend = "sqlite"
    cfg.metadata.path = str(tmp_path / "meta.db")
    cfg.transformation_cache.backend = "sqlite"
    cfg.transformation_cache.db_path = str(tmp_path / "tx.db")
    cfg.artifact_cache.root = str(tmp_path / "art")
    eng = Engine(cfg)
    await eng.infra.meta.connect()
    await eng.infra.meta.init_schema()
    return eng


# --- Engine wiring + bind_remover ---


async def test_engine_forwards_to_ingest(tmp_path):
    eng = await _build_engine(tmp_path)
    try:
        assert isinstance(eng.ingest, IngestOrchestrator)
        assert eng.ingest._obj is eng.objects
        assert eng.ingest._pipeline is eng.pipeline
        assert eng.ingest._factory is eng.connector_factory
        assert eng.ingest._art is eng.artifacts
        assert eng.ingest._infra is eng.infra
        assert eng.ingest._ns == eng.ns
        # __init__ wired bind_remover to Engine.remove_connector
        assert eng.ingest._remove_connector == eng.remove_connector
    finally:
        await eng.infra.meta.close()


def test_bind_remover_sets_callable():
    class _Cfg:
        namespace = "ns1"

    calls: list[str] = []

    async def _remover(uri: str) -> None:
        calls.append(uri)

    orch = IngestOrchestrator(_Cfg(), object(), object(), object(), object(), object())
    assert orch._remove_connector is None
    orch.bind_remover(_remover)
    assert orch._remove_connector is _remover


# --- ObjectIndexer four exits ---


async def test_indexer_deleted():
    obj, mil, art = _RecObjects(), _RecMilvus(), _RecArtifacts()
    plug = _FakePlugin()
    idx = ObjectIndexer(obj, art, _FakeInfra(mil), _FakePipeline(), "ns1")
    r = await idx.handle(plug, "file:///r", _task("deleted"))
    assert r is None
    assert obj.deleted_rows == [("c1", "/a.md")]
    assert mil.deletes == [("ns1", "file:///r", "file:///r/a.md")]
    assert art.dropped == [("ns1", "file:///r/a.md")]
    assert plug.deleted == ["/a.md"]
    assert plug.indexed == []  # on_object_indexed NOT called for deletes


async def test_indexer_renamed_reuses_vectors():
    old_chunks = [
        {"locator": "L1", "content": "c", "dense_vec": [0.1], "chunk_kind": "text", "metadata": {}},
        {
            "locator": "L2",
            "content": "d",
            "dense_vec": [0.2],
            "chunk_kind": "text",
            "metadata": None,
        },
    ]
    obj, mil, art = _RecObjects(), _RecMilvus(chunks_for=old_chunks), _RecArtifacts()
    plug = _FakePlugin(stat=_stat("/a.md"))
    idx = ObjectIndexer(obj, art, _FakeInfra(mil), _FakePipeline(), "ns1")
    r = await idx.handle(plug, "file:///r", _task("renamed", "/a.md", old_uri="/old.md"))
    assert r is None  # reused vectors - whole object done
    # old milvus chunks deleted, new rows upserted
    assert ("ns1", "file:///r", "file:///r/old.md") in mil.deletes
    assert len(mil.upserts) == 1 and len(mil.upserts[0][1]) == 2
    # artifacts renamed, old object row deleted, new row written 'indexed'
    assert art.renamed == [("ns1", "file:///r/old.md", "file:///r/a.md")]
    assert ("c1", "/old.md") in obj.deleted_rows
    assert any(w[4] == "indexed" and w[5] == 2 for w in obj.written_rows)
    assert plug.indexed == ["/a.md"]


async def test_indexer_renamed_empty_falls_through_to_metadata():
    obj, mil, art = _RecObjects(), _RecMilvus(chunks_for=[]), _RecArtifacts()
    plug = _FakePlugin(okind="document")  # routes_to_pipeline default True -> would be pipeline
    # force NOT routing so the fallthrough lands on metadata-only
    pipe = _FakePipeline(routes=False)
    idx = ObjectIndexer(obj, art, _FakeInfra(mil), pipe, "ns1")
    r = await idx.handle(plug, "file:///r", _task("renamed", "/a.md", old_uri="/old.md"))
    assert r is None
    # old refs cleaned
    assert ("ns1", "file:///r", "file:///r/old.md") in mil.deletes
    assert ("c1", "/old.md") in obj.deleted_rows
    # then metadata-only tail: purge new + write 'not_indexed'/0
    assert ("ns1", "file:///r", "file:///r/a.md") in mil.deletes
    assert any(w[4] == "not_indexed" and w[5] == 0 for w in obj.written_rows)
    assert plug.indexed == ["/a.md"]


async def test_indexer_pipeline_returns_deferred():
    obj, mil, art = _RecObjects(), _RecMilvus(), _RecArtifacts()
    plug = _FakePlugin(okind="document", indexable=True, stat=_stat("/a.md"))
    pipe = _FakePipeline(routes=True)
    idx = ObjectIndexer(obj, art, _FakeInfra(mil), pipe, "ns1")
    r = await idx.handle(plug, "file:///r", _task("added"))
    assert r == "deferred"
    # stash_finalize got the exact tuple (cid, connector_uri, relpath, st, indexable, plugin, task_id)
    assert len(pipe.stashed) == 1
    full_uri, ctx_tuple = pipe.stashed[0]
    assert full_uri == "file:///r/a.md"
    cid, connector_uri, relpath, st, indexable, plugin, task_id = ctx_tuple
    assert (cid, connector_uri, relpath, indexable, plugin, task_id) == (
        "c1",
        "file:///r",
        "/a.md",
        True,
        plug,
        "t1",
    )
    assert st is plug._stat
    assert pipe.pumped == [("file:///r", "/a.md", "file:///r/a.md", "document")]


async def test_indexer_metadata_only_for_binary():
    obj, mil, art = _RecObjects(), _RecMilvus(), _RecArtifacts()
    plug = _FakePlugin(okind="binary", indexable=True)  # indexable forced False by okind
    pipe = _FakePipeline(routes=True)  # would route, but binary is not indexable
    idx = ObjectIndexer(obj, art, _FakeInfra(mil), pipe, "ns1")
    r = await idx.handle(plug, "file:///r", _task("added"))
    assert r is None
    # metadata-only: purge + write 'not_indexed'/0, on_object_indexed
    assert ("ns1", "file:///r", "file:///r/a.md") in mil.deletes
    assert any(w[4] == "not_indexed" and w[5] == 0 and w[3] is False for w in obj.written_rows)
    assert plug.indexed == ["/a.md"]
    # pipeline was NOT taken
    assert pipe.stashed == [] and pipe.pumped == []


async def test_indexer_metadata_only_when_indexable_opted_out():
    obj, mil, art = _RecObjects(), _RecMilvus(), _RecArtifacts()
    plug = _FakePlugin(okind="document", indexable=False)  # [[objects]] indexable=false
    pipe = _FakePipeline(routes=True)
    idx = ObjectIndexer(obj, art, _FakeInfra(mil), pipe, "ns1")
    r = await idx.handle(plug, "file:///r", _task("added"))
    assert r is None
    assert any(w[3] is False and w[4] == "not_indexed" for w in obj.written_rows)
    assert plug.indexed == ["/a.md"]
    assert pipe.stashed == [] and pipe.pumped == []


async def test_indexer_metadata_only_for_non_pipeline_okind():
    obj, mil, art = _RecObjects(), _RecMilvus(), _RecArtifacts()
    plug = _FakePlugin(okind="binary", indexable=True)
    pipe = _FakePipeline(routes=False)
    idx = ObjectIndexer(obj, art, _FakeInfra(mil), pipe, "ns1")
    r = await idx.handle(plug, "file:///r", _task("modified"))
    assert r is None
    assert any(w[4] == "not_indexed" and w[5] == 0 for w in obj.written_rows)
    assert plug.indexed == ["/a.md"]
