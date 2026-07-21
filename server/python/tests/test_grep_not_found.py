"""GET /v1/grep on a path that doesn't exist under its connector must 404, like
ls/cat -- not silently return zero results. Unlike ls/cat, grep's pushdown/BM25/
linear-scan dispatch never touches the target path directly, so a missing path
just looks like a real search with no matches unless the engine checks existence
up front.
"""

from __future__ import annotations

import pytest

from mfs_server.config import ServerConfig
from mfs_server.engine.engine import Engine


class _FakePlugin:
    """Mimics FilePlugin.stat: raises FileNotFoundError for anything but /exists.txt."""

    def __init__(self):
        self.closed = False

    async def stat(self, rel):
        if rel != "/exists.txt":
            raise FileNotFoundError(rel)
        from mfs_server.connectors.base import PathStat

        return PathStat(path=rel, type="file", media_type="text/plain", size_hint=1)

    async def grep(self, pattern, rel, options):
        return None  # no pushdown -> engine falls through to BM25/linear scan

    async def close(self) -> None:
        self.closed = True


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


async def test_grep_missing_path_raises_file_not_found(tmp_path) -> None:
    eng = await _build_engine(tmp_path)
    plugin = _FakePlugin()

    async def fake_open_path(path: str):
        return "cid", "file://local/root", "/does/not/exist", plugin

    eng.reads.open_path = fake_open_path  # type: ignore[method-assign]

    with pytest.raises(FileNotFoundError):
        await eng.grep("needle", "file://local/root/does/not/exist")

    assert plugin.closed
    await eng.infra.meta.close()
