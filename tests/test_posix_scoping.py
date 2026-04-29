"""Regression tests for POSIX-style scoping of ``mfs search`` and ``mfs grep``.

Both commands used to silently default to the current working directory when
no ``--path`` / positional path / ``--all`` was given. That default killed
pipelines run from tmp dirs outside the indexed corpus (silent "No results").

New contract (matches Linux ``grep`` semantics):

    tty stdin + no path + no --all   →  exit 2 with a clear error
    tty stdin + positional path      →  scope to that path
    tty stdin + --path X (alias)     →  scope to X (kept for back-compat)
    tty stdin + --all                →  whole index
    piped stdin                      →  existing 4-branch handling unchanged

Tests stub out the Searcher / embedder / store so they run without a live
Milvus backend.
"""

from __future__ import annotations

from click.testing import CliRunner

from mfs import cli as cli_mod
from mfs.cli import main


class _StubEmbedder:
    model_name = "stub"
    dimension = 8
    batch_size = 4

    def embed(self, texts):
        return [[0.0] * 8 for _ in texts]


class _StubStore:
    def close(self) -> None:  # pragma: no cover - defensive
        pass


class _SpyingSearcher:
    instances: list["_SpyingSearcher"] = []

    def __init__(self, store, embedder, scanner=None):
        self.search_calls: list[dict] = []
        self.grep_calls: list[dict] = []
        _SpyingSearcher.instances.append(self)

    def search(self, query, mode, path_filter, top_k):
        self.search_calls.append({
            "query": query, "mode": mode,
            "path_filter": path_filter, "top_k": top_k,
        })
        return []

    def grep(self, pattern, path=None, context_lines=0, case_insensitive=False):
        self.grep_calls.append({
            "pattern": pattern, "path": path,
            "context_lines": context_lines,
            "case_insensitive": case_insensitive,
        })
        return []


def _install_stubs(monkeypatch):
    _SpyingSearcher.instances = []
    monkeypatch.setattr(cli_mod, "_build_embedder", lambda _cfg: _StubEmbedder())
    monkeypatch.setattr(cli_mod, "_build_store",
                        lambda _cfg, _dim, retry_on_lock=False: _StubStore())
    monkeypatch.setattr(cli_mod, "Searcher", _SpyingSearcher)
    monkeypatch.setattr(cli_mod, "stdin_has_data", lambda: False)


def _latest():
    assert _SpyingSearcher.instances, "Searcher was never constructed"
    return _SpyingSearcher.instances[-1]


# --------------------------------------------------------------------- search


def test_search_no_path_no_all_no_pipe_errors(mfs_home, monkeypatch, tmp_path):
    _install_stubs(monkeypatch)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["search", "x"])
    assert result.exit_code == 2
    combined = result.output + (result.stderr or "")
    assert "no path specified" in combined.lower()
    assert _SpyingSearcher.instances == []


def test_search_all_flag_unfilters(mfs_home, monkeypatch):
    _install_stubs(monkeypatch)
    runner = CliRunner()
    result = runner.invoke(main, ["search", "x", "--all"])
    assert result.exit_code == 0, result.output
    calls = _latest().search_calls
    assert len(calls) == 1
    assert calls[0]["path_filter"] is None


def test_search_positional_path_scopes(mfs_home, monkeypatch, tmp_path):
    _install_stubs(monkeypatch)
    runner = CliRunner()
    result = runner.invoke(main, ["search", "x", str(tmp_path)])
    assert result.exit_code == 0, result.output
    calls = _latest().search_calls
    assert len(calls) == 1
    assert calls[0]["path_filter"] == str(tmp_path.resolve())


def test_search_path_option_alias(mfs_home, monkeypatch, tmp_path):
    _install_stubs(monkeypatch)
    runner = CliRunner()
    result = runner.invoke(main, ["search", "x", "--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    calls = _latest().search_calls
    assert len(calls) == 1
    assert calls[0]["path_filter"] == str(tmp_path.resolve())


# ----------------------------------------------------------------------- grep


def test_grep_no_path_no_all_no_pipe_errors(mfs_home, monkeypatch, tmp_path):
    _install_stubs(monkeypatch)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["grep", "foo"])
    assert result.exit_code == 2
    combined = result.output + (result.stderr or "")
    assert "no path specified" in combined.lower()
    assert _latest().grep_calls == []


def test_grep_all_flag_whole_index(mfs_home, monkeypatch):
    _install_stubs(monkeypatch)
    runner = CliRunner()
    result = runner.invoke(main, ["grep", "foo", "--all"])
    assert result.exit_code == 0, result.output
    calls = _latest().grep_calls
    assert len(calls) == 1
    # path=None means "whole index" now.
    assert calls[0]["path"] is None


def test_grep_positional_path_scopes(mfs_home, monkeypatch, tmp_path):
    _install_stubs(monkeypatch)
    runner = CliRunner()
    result = runner.invoke(main, ["grep", "foo", str(tmp_path)])
    assert result.exit_code == 0, result.output
    calls = _latest().grep_calls
    assert len(calls) == 1
    assert str(calls[0]["path"]) == str(tmp_path.resolve())


def test_grep_path_option_alias(mfs_home, monkeypatch, tmp_path):
    _install_stubs(monkeypatch)
    runner = CliRunner()
    result = runner.invoke(main, ["grep", "foo", "--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    calls = _latest().grep_calls
    assert len(calls) == 1
    assert str(calls[0]["path"]) == str(tmp_path.resolve())
