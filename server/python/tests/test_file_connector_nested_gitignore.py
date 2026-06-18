"""The file connector honors nested .gitignore/.mfsignore (one per directory), like git —
not only the connector root's. A subproject that ignores its build dir via its own
.gitignore (e.g. a Rust `server-rs/.gitignore` with `/target`) must not have that build
output walked and indexed.
"""

from __future__ import annotations

from typing import Any

from mfs_server.connectors.base import ConnectorContext
from mfs_server.connectors.file.plugin import FileConfig, FilePlugin, _translate_ignore_line


class _MemoryState:
    async def get(self, key: str) -> Any | None:
        return None

    async def set(self, key: str, value: Any) -> None:
        return None

    async def delete(self, key: str) -> None:
        return None

    async def checkpoint(self) -> None:
        return None


def _plugin(root) -> FilePlugin:
    ctx = ConnectorContext(_MemoryState(), "cid", "ns")
    return FilePlugin(FileConfig(root=str(root)), None, ctx=ctx)


def _write(p, text="x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_translate_ignore_line():
    # root file: unchanged
    assert _translate_ignore_line("", "/target") == "/target"
    assert _translate_ignore_line("", "*.tmp") == "*.tmp"
    # nested anchored (leading slash or embedded slash) -> scoped to the dir
    assert _translate_ignore_line("sub", "/target") == "sub/target"
    assert _translate_ignore_line("a/b", "foo/bar") == "a/b/foo/bar"
    # nested floating -> matches any depth under the dir
    assert _translate_ignore_line("sub", "*.so") == "sub/**/*.so"
    assert _translate_ignore_line("sub", "build/") == "sub/**/build/"
    # negation + comments/blanks
    assert _translate_ignore_line("sub", "!keep.log") == "!sub/**/keep.log"
    assert _translate_ignore_line("sub", "  # comment") is None
    assert _translate_ignore_line("sub", "") is None


def test_scan_honors_nested_gitignore(tmp_path):
    _write(tmp_path / "a.txt")
    # nested ignore file: anchored /target (this dir only) + floating *.tmp (any depth)
    _write(tmp_path / "sub" / ".gitignore", "/target\n*.tmp\n")
    _write(tmp_path / "sub" / "keep.py")
    _write(tmp_path / "sub" / "junk.tmp")  # floating *.tmp, direct child
    _write(tmp_path / "sub" / "target" / "bin.dat")  # anchored /target
    _write(tmp_path / "sub" / "deep" / "also.tmp")  # floating *.tmp, nested
    _write(tmp_path / "sub" / "deep" / "ok.txt")
    # anchoring: sub's "/target" must NOT ignore a target/ elsewhere
    _write(tmp_path / "other" / "target" / "thing.dat")
    # DEFAULT_IGNORE still applies
    _write(tmp_path / "node_modules" / "pkg.js")

    got = set(_plugin(tmp_path)._scan().keys())
    assert got == {
        "/a.txt",
        "/sub/.gitignore",  # ignore files are themselves walked (existing behavior)
        "/sub/keep.py",
        "/sub/deep/ok.txt",
        "/other/target/thing.dat",
    }


def test_scan_nested_negation(tmp_path):
    _write(tmp_path / ".gitignore", "*.log\n!important.log\n")
    _write(tmp_path / "a.log")
    _write(tmp_path / "important.log")
    got = set(_plugin(tmp_path)._scan().keys())
    assert "/important.log" in got
    assert "/a.log" not in got


def test_scan_never_descends_into_ignored_dir(tmp_path):
    # a nested-ignored dir with many files must not be walked at all
    _write(tmp_path / "keep.txt")
    _write(tmp_path / "pkg" / ".gitignore", "/vendor\n")
    for i in range(50):
        _write(tmp_path / "pkg" / "vendor" / f"f{i}.dat")
    _write(tmp_path / "pkg" / "src.py")
    got = set(_plugin(tmp_path)._scan().keys())
    assert got == {"/keep.txt", "/pkg/.gitignore", "/pkg/src.py"}


async def test_list_honors_nested_gitignore(tmp_path):
    _write(tmp_path / "sub" / ".gitignore", "/target\n*.tmp\n")
    _write(tmp_path / "sub" / "keep.py")
    _write(tmp_path / "sub" / "junk.tmp")
    _write(tmp_path / "sub" / "target" / "bin.dat")
    names = {e.name for e in await _plugin(tmp_path).list("/sub")}
    assert names == {".gitignore", "keep.py"}  # target/ and junk.tmp excluded
