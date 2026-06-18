"""Startup orphan GC: delete Milvus chunks that no committed `objects` row points at,
without touching directory summaries (which legitimately have no objects row).

The GC is cost-guarded — it compares the Milvus row total against the sum of committed
chunk_counts and only scans/deletes when there is genuine excess, so a healthy index pays
just a couple of count() aggregates. Both the count and the scan are scoped to non-summary
chunks; a real Milvus Lite check guards that scoping so the GC can never eat a summary.
"""

from __future__ import annotations

from mfs_server.config import ServerConfig
from mfs_server.connectors.base import PathStat
from mfs_server.engine.engine import Engine
from mfs_server.storage.milvus import MilvusStore


class _FakeMilvus:
    GC_SCOPE_EXPR = 'chunk_kind != "directory_summary"'

    def __init__(self, total: int, present: list[tuple[str, str]]):
        self._total = total
        self._present = present
        self.deletes: list[tuple[str, str]] = []
        self.distinct_calls = 0

    def count(self, ns, expr: str = "") -> int:
        return self._total

    def distinct_objects(self, ns) -> list[tuple[str, str]]:
        self.distinct_calls += 1
        return list(self._present)

    def delete_by_object(self, ns, connector_uri, object_uri) -> None:
        self.deletes.append((connector_uri, object_uri))


async def _engine_with(tmp_path, fake) -> Engine:
    cfg = ServerConfig()
    cfg.metadata.backend = "sqlite"
    cfg.metadata.path = str(tmp_path / "meta.db")
    cfg.transformation_cache.backend = "sqlite"
    cfg.transformation_cache.db_path = str(tmp_path / "tx.db")
    cfg.artifact_cache.root = str(tmp_path / "art")
    eng = Engine(cfg)
    eng.milvus = fake
    await eng.meta.connect()
    await eng.meta.init_schema()
    return eng


def _stat(rel: str) -> PathStat:
    return PathStat(
        path=rel, type="file", media_type="text/markdown", size_hint=10, fingerprint="fp:" + rel
    )


async def _seed_connector(eng: Engine, root_uri: str) -> str:
    return await eng.register_or_get_connector(
        root_uri, "file", {"root": "/x", "client_id": "local"}
    )


async def test_gc_noop_on_healthy_index(tmp_path):
    # total == sum(chunk_count): nothing to do, and the distinct scan must not run
    fake = _FakeMilvus(total=5, present=[("file:///repo", "file:///repo/should-not-scan.md")])
    eng = await _engine_with(tmp_path, fake)
    cid = await _seed_connector(eng, "file:///repo")
    await eng._write_object_row(cid, "/a.md", _stat("/a.md"), True, "indexed", 2)
    await eng._write_object_row(cid, "/b.md", _stat("/b.md"), True, "indexed", 3)

    assert await eng._gc_orphan_chunks() == 0
    assert fake.deletes == []
    assert fake.distinct_calls == 0  # count-first guard: no scan on a healthy index
    await eng.meta.close()


async def test_gc_purges_orphans_keeps_valid(tmp_path):
    present = [
        ("file:///repo", "file:///repo/a.md"),  # committed -> keep
        ("file:///repo", "file:///repo/ghost.md"),  # cancelled orphan -> purge
        ("file:///gone", "file:///gone/x.md"),  # removed-connector orphan -> purge
    ]
    fake = _FakeMilvus(total=7, present=present)  # 7 > expected(5) -> triggers the scan
    eng = await _engine_with(tmp_path, fake)
    cid = await _seed_connector(eng, "file:///repo")
    await eng._write_object_row(cid, "/a.md", _stat("/a.md"), True, "indexed", 5)

    assert await eng._gc_orphan_chunks() == 2
    assert sorted(fake.deletes) == sorted(
        [("file:///repo", "file:///repo/ghost.md"), ("file:///gone", "file:///gone/x.md")]
    )
    await eng.meta.close()


def test_gc_scope_excludes_directory_summaries(tmp_path):
    """Real Milvus Lite: the GC's count + scan must skip directory_summary chunks, which
    have no objects row and would otherwise look like orphans."""
    cfg = ServerConfig()
    cfg.milvus.uri = str(tmp_path / "milvus.db")
    store = MilvusStore(cfg)
    store.connect()
    store.ensure_collection("default")
    dim = store.dim

    def row(chunk_id, connector_uri, object_uri, kind):
        return {
            "chunk_id": chunk_id,
            "namespace_id": "default",
            "connector_uri": connector_uri,
            "object_uri": object_uri,
            "content": "x",
            "dense_vec": [0.1] * dim,
            "chunk_kind": kind,
            "indexed_at": 0,
            "locator": None,
            "metadata": None,
        }

    store.upsert(
        "default",
        [
            row("a1", "file:///r", "file:///r/a.md", "body"),
            row("a2", "file:///r", "file:///r/a.md", "body"),
            row("d1", "file:///r", "file:///r", "directory_summary"),
        ],
    )

    # count scoped to non-summary chunks excludes the directory summary
    assert store.count("default", store.GC_SCOPE_EXPR) == 2
    # the scan reports only the real object, never the summary's object_uri
    assert store.distinct_objects("default") == [("file:///r", "file:///r/a.md")]
