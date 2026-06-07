from __future__ import annotations

from typing import Any

import pytest

from mfs_server.connectors.base import ConnectorContext, GrepOptions
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
async def test_file_grep_scans_long_source_line_tail(tmp_path):
    marker = "TAIL_LONG_TOKENIZATION_MARKER_BEYOND_INDEX_CAP"
    path = tmp_path / "long.txt"
    path.write_text("BEGIN_" + ("TOKEN" * 20_000) + "_" + marker + "\n", encoding="utf-8")

    plugin = _plugin(tmp_path)
    gen = await plugin.grep(marker, "/", GrepOptions(pattern=marker))
    assert gen is not None

    matches = [match async for match in gen]
    assert len(matches) == 1
    assert matches[0].path == "/long.txt"
    assert matches[0].line_no == 1
    assert marker in matches[0].content


@pytest.mark.anyio
async def test_file_grep_respects_directory_scope_and_line_locator(tmp_path):
    (tmp_path / "keep").mkdir()
    (tmp_path / "skip").mkdir()
    (tmp_path / "keep" / "a.txt").write_text("first\nneedle here\n", encoding="utf-8")
    (tmp_path / "skip" / "b.txt").write_text("needle outside scope\n", encoding="utf-8")

    plugin = _plugin(tmp_path)
    gen = await plugin.grep("needle", "/keep", GrepOptions(pattern="needle"))
    assert gen is not None

    matches = [match async for match in gen]
    assert [(match.path, match.line_no, match.content) for match in matches] == [
        ("/keep/a.txt", 2, "needle here")
    ]
