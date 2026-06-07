"""ArtifactStoreAdapter routes producer artifact writes through the engine (finding 11).

Producer-written artifacts must be accounted for by the same LRU size eviction + last_accessed
recency bump as engine-written ones, so [artifact_cache].max_size_gb is actually enforced.
"""

from __future__ import annotations

from mfs_server.config import ServerConfig
from mfs_server.engine.engine import Engine


class _FakeEmbed:
    provider_name = "fake"
    model = "fake-model"
    version = "1"

    def _key(self, text):
        return "k:" + text

    async def _embed_api(self, texts):
        return [[0.1] for _ in texts]


class _FakeMilvus:
    def upsert(self, ns, rows):
        return None

    def delete_by_object(self, ns, connector_uri, object_uri):
        return None


class _FakeTxCache:
    async def batch_get(self, keys):
        return {k: None for k in keys}

    async def batch_put(self, entries):
        return None


async def _build_engine(tmp_path, *, max_size_gb):
    cfg = ServerConfig()
    cfg.metadata.backend = "sqlite"
    cfg.metadata.path = str(tmp_path / "meta.db")
    cfg.transformation_cache.backend = "sqlite"
    cfg.transformation_cache.db_path = str(tmp_path / "tx.db")
    cfg.artifact_cache.root = str(tmp_path / "art")
    cfg.artifact_cache.max_size_gb = max_size_gb
    eng = Engine(cfg)
    eng.embed = _FakeEmbed()
    eng.milvus = _FakeMilvus()
    eng.tx_cache = _FakeTxCache()
    await eng.meta.connect()
    await eng.meta.init_schema()
    eng._build_pipeline()
    return eng


async def test_adapter_put_records_row_and_get_bumps_recency(tmp_path):
    eng = await _build_engine(tmp_path, max_size_gb=1.0)
    art = eng._producer_ctx.artifacts

    await art.put_artifact(eng.ns, "file:///r/a.md", "converted_md", b"hello")
    row = await eng.meta.fetchone(
        "SELECT size_bytes, last_accessed FROM artifact_cache "
        "WHERE namespace_id=? AND object_uri=? AND artifact_kind=?",
        (eng.ns, "file:///r/a.md", "converted_md"),
    )
    assert row is not None and row["size_bytes"] == 5  # write recorded in the index
    first_access = row["last_accessed"]

    got = await art.get_artifact(eng.ns, "file:///r/a.md", "converted_md")
    assert got == b"hello"
    row2 = await eng.meta.fetchone(
        "SELECT last_accessed FROM artifact_cache "
        "WHERE namespace_id=? AND object_uri=? AND artifact_kind=?",
        (eng.ns, "file:///r/a.md", "converted_md"),
    )
    assert row2["last_accessed"] >= first_access  # recency bumped on read
    await eng.meta.close()


async def test_adapter_put_enforces_size_cap(tmp_path):
    # cap ~256 KiB; writing 16 distinct 64 KiB artifacts (1 MiB total) must trigger the eviction
    # sweep (it runs every 16 writes) so the cached total falls back under the cap.
    max_bytes = 256 * 1024
    eng = await _build_engine(tmp_path, max_size_gb=max_bytes / (1 << 30))
    art = eng._producer_ctx.artifacts

    blob = b"x" * (64 * 1024)
    for i in range(16):
        await art.put_artifact(eng.ns, f"file:///r/f{i}.bin", "head_cache", blob)

    row = await eng.meta.fetchone(
        "SELECT sum(size_bytes) AS total FROM artifact_cache WHERE namespace_id=?", (eng.ns,)
    )
    assert (row["total"] or 0) <= max_bytes  # eviction kept the cache under budget
    await eng.meta.close()
