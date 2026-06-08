from __future__ import annotations

import pytest

from mfs_server.config import ServerConfig
from mfs_server.engine.engine import Engine
from mfs_server.connectors.registry import load_builtin


class _NoopMilvus:
    def delete_by_connector(self, *args, **kwargs):
        return None


class _NoopReduce:
    def register_job(self, *args, **kwargs):
        return None

    def on_sync_done(self, *args, **kwargs):
        return None

    def evict_job(self, *args, **kwargs):
        return None


async def _build_engine(tmp_path) -> Engine:
    load_builtin()
    cfg = ServerConfig()
    cfg.metadata.backend = "sqlite"
    cfg.metadata.path = str(tmp_path / "metadata.db")
    cfg.transformation_cache.backend = "sqlite"
    cfg.transformation_cache.db_path = str(tmp_path / "tx.db")
    cfg.artifact_cache.root = str(tmp_path / "artifacts")
    cfg.milvus.uri = str(tmp_path / "milvus.db")
    eng = Engine(cfg)
    eng.milvus = _NoopMilvus()
    eng._reduce = _NoopReduce()
    await eng.meta.connect()
    await eng.meta.init_schema()
    return eng


async def test_remove_rejects_registered_child_path(tmp_path):
    eng = await _build_engine(tmp_path)
    try:
        root = tmp_path / "repo"
        (root / "src").mkdir(parents=True)
        root_uri = f"file://local{root}"
        await eng.register_or_get_connector(
            root_uri,
            "file",
            {"root": str(root), "client_id": "local"},
        )

        with pytest.raises(ValueError, match="remove_requires_connector_root"):
            await eng.remove_connector(str(root / "src"))
    finally:
        await eng.meta.close()


async def test_remove_rejects_unregistered_target(tmp_path):
    eng = await _build_engine(tmp_path)
    try:
        with pytest.raises(ValueError, match="remove_requires_connector_root"):
            await eng.remove_connector(str(tmp_path / "missing"))
    finally:
        await eng.meta.close()


async def test_failed_initial_add_rolls_back_connector_registration(tmp_path):
    eng = await _build_engine(tmp_path)
    try:
        missing = tmp_path / "missing"

        with pytest.raises(ValueError, match="connector_unhealthy"):
            await eng.add(str(missing), process=False)

        for table in ("connectors", "connector_jobs", "object_tasks", "file_state"):
            row = await eng.meta.fetchone(f"SELECT count(*) AS n FROM {table}")
            assert row["n"] == 0
    finally:
        await eng.meta.close()
