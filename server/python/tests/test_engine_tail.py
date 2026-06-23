from __future__ import annotations

import pytest

from mfs_server.config import ServerConfig
from mfs_server.engine.engine import Engine


class _StructuredPlugin:
    def __init__(self, okind: str):
        self._okind = okind
        self.closed = False

    def object_kind_of(self, rel: str) -> str:
        return self._okind

    async def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize("okind", ["table_rows", "record_collection", "message_stream"])
async def test_tail_rejects_unstable_structured_objects(tmp_path, okind: str) -> None:
    cfg = ServerConfig()
    cfg.metadata.backend = "sqlite"
    cfg.metadata.path = str(tmp_path / "meta.db")
    cfg.transformation_cache.backend = "sqlite"
    cfg.transformation_cache.db_path = str(tmp_path / "tx.db")
    cfg.artifact_cache.root = str(tmp_path / "art")
    eng = Engine(cfg)
    plugin = _StructuredPlugin(okind)

    async def fake_open_path(path: str):
        return "cid", "postgres://db", "/rows.jsonl", plugin

    eng._open_path = fake_open_path  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="tail_unsupported"):
        await eng.tail("postgres://db/rows.jsonl", 5)

    assert plugin.closed
