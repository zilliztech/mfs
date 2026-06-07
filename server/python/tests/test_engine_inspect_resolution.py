from __future__ import annotations

from mfs_server.config import ServerConfig
from mfs_server.engine.engine import Engine


async def _build_engine(tmp_path):
    cfg = ServerConfig()
    cfg.metadata.backend = "sqlite"
    cfg.metadata.path = str(tmp_path / "metadata.db")
    cfg.transformation_cache.backend = "sqlite"
    cfg.transformation_cache.db_path = str(tmp_path / "tx.db")
    cfg.artifact_cache.root = str(tmp_path / "artifacts")
    eng = Engine(cfg)
    await eng.meta.connect()
    await eng.meta.init_schema()
    return eng


async def test_inspect_matches_uploaded_child_uri_to_connector(tmp_path):
    eng = await _build_engine(tmp_path)
    try:
        staging = tmp_path / "staging"
        staging.mkdir()
        root_uri = "file://client-1/tmp/project"
        await eng.register_or_get_connector(
            root_uri,
            "file",
            {"root": str(staging), "client_id": "client-1", "upload_mode": True},
        )

        root = await eng.inspect(root_uri)
        child = await eng.inspect(root_uri + "/src/app.py")

        assert root is not None
        assert child is not None
        assert child["root_uri"] == root["root_uri"] == root_uri
    finally:
        await eng.meta.close()


async def test_inspect_matches_local_child_path_to_connector(tmp_path):
    eng = await _build_engine(tmp_path)
    try:
        root = tmp_path / "repo"
        nested = root / "src"
        nested.mkdir(parents=True)
        child_path = nested / "app.py"
        child_path.write_text("print('hello')\n")
        root_uri = f"file://local{root}"
        await eng.register_or_get_connector(
            root_uri,
            "file",
            {"root": str(root), "client_id": "local"},
        )

        inspected = await eng.inspect(str(child_path))

        assert inspected is not None
        assert inspected["root_uri"] == root_uri
    finally:
        await eng.meta.close()
