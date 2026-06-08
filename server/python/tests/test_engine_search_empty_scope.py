from __future__ import annotations

from mfs_server.config import ServerConfig
from mfs_server.engine.engine import Engine


class _FailingEmbed:
    async def batch_embed(self, texts):
        raise AssertionError("empty search should not call the embedder")


class _FailingMilvus:
    def sparse_search(self, *args, **kwargs):
        raise AssertionError("empty search should not call Milvus")

    def search_dense(self, *args, **kwargs):
        raise AssertionError("empty search should not call Milvus")

    def hybrid_search(self, *args, **kwargs):
        raise AssertionError("empty search should not call Milvus")


async def _build_engine(tmp_path):
    cfg = ServerConfig()
    cfg.metadata.backend = "sqlite"
    cfg.metadata.path = str(tmp_path / "metadata.db")
    cfg.transformation_cache.backend = "sqlite"
    cfg.transformation_cache.db_path = str(tmp_path / "tx.db")
    cfg.artifact_cache.root = str(tmp_path / "artifacts")
    eng = Engine(cfg)
    eng.embed = _FailingEmbed()
    eng.milvus = _FailingMilvus()
    await eng.meta.connect()
    await eng.meta.init_schema()
    return eng


async def test_search_empty_namespace_returns_without_query_backend(tmp_path):
    eng = await _build_engine(tmp_path)
    try:
        assert await eng.search("mfs-e2e-empty-query", top_k=3) == []
    finally:
        await eng.meta.close()


async def test_search_unregistered_scope_returns_without_query_backend(tmp_path):
    eng = await _build_engine(tmp_path)
    try:
        assert (
            await eng.search(
                "mfs-e2e-empty-query",
                connector_uri="file://local/mfs-e2e-empty",
                object_prefix=None,
                top_k=3,
            )
            == []
        )
    finally:
        await eng.meta.close()
