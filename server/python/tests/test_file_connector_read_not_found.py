"""read() on a nonexistent path (used by mfs head/tail/cat) must raise a plain
FileNotFoundError carrying only the connector-relative path -- not the raw OS
error, whose str() bakes in the absolute local filesystem path. stat()/list()/
grep() in this same file already guard against that leak; read() must match.
"""

from __future__ import annotations

from typing import Any

import pytest

from mfs_server.connectors.base import ConnectorContext
from mfs_server.connectors.file.plugin import FileConfig, FilePlugin


class MemoryState:
    async def get(self, key: str) -> Any | None:
        return None

    async def set(self, key: str, value: Any) -> None:
        return None

    async def delete(self, key: str) -> None:
        return None

    async def checkpoint(self) -> None:
        return None


def _plugin(root) -> FilePlugin:
    ctx = ConnectorContext(MemoryState(), "connector-id", "namespace-id")
    return FilePlugin(FileConfig(root=str(root)), None, ctx=ctx)


@pytest.mark.anyio
async def test_file_read_missing_path_raises_relative_not_found(tmp_path):
    plugin = _plugin(tmp_path)

    with pytest.raises(FileNotFoundError) as excinfo:
        async for _ in plugin.read("/does/not/exist.txt"):
            pass

    assert str(excinfo.value) == "/does/not/exist.txt"
    assert str(tmp_path) not in str(excinfo.value)


@pytest.mark.anyio
async def test_file_read_missing_path_with_range_raises_relative_not_found(tmp_path):
    from mfs_server.connectors.base import Range

    plugin = _plugin(tmp_path)

    with pytest.raises(FileNotFoundError) as excinfo:
        async for _ in plugin.read("/does/not/exist.txt", range=Range(start=0, end=5)):
            pass

    assert str(excinfo.value) == "/does/not/exist.txt"
    assert str(tmp_path) not in str(excinfo.value)
