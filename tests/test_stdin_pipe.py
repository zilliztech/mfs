"""Regression tests for `mfs search` stdin-pipe behavior.

Bug: `mfs search` used to silently fall back to searching the indexed corpus
when stdin was piped but had neither ``::mfs:`` headers nor content. The fix
makes the four stdin states explicit:

  1. tty (no pipe)              → search indexed corpus (existing)
  2. pipe + ``::mfs:`` headers  → filter corpus by ``source`` (existing)
  3. pipe + plain text          → temporary dense search over stdin itself
  4. pipe + empty               → no results (no corpus fallback)

Tests mock the Milvus store / embedder / Searcher so they run without a live
backend. ``CliRunner.invoke(input=...)`` replaces ``sys.stdin`` with a StringIO
whose ``isatty()`` is False — i.e. CliRunner always looks like a pipe. To
exercise the tty branch we monkey-patch ``mfs.cli.stdin_has_data`` → False.
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
    """Records every ``.search()`` invocation so tests can assert on it."""

    instances: list["_SpyingSearcher"] = []

    def __init__(self, store, embedder, scanner=None):
        self.calls: list[dict] = []
        _SpyingSearcher.instances.append(self)

    def search(self, query, mode, path_filter, top_k):
        self.calls.append({"query": query, "mode": mode,
                           "path_filter": path_filter, "top_k": top_k})
        return []


def _install_stubs(monkeypatch):
    _SpyingSearcher.instances = []
    monkeypatch.setattr(cli_mod, "_build_embedder", lambda _cfg: _StubEmbedder())
    monkeypatch.setattr(cli_mod, "_build_store",
                        lambda _cfg, _dim, retry_on_lock=False: _StubStore())
    monkeypatch.setattr(cli_mod, "Searcher", _SpyingSearcher)


def _calls():
    assert _SpyingSearcher.instances, "Searcher was never constructed"
    return _SpyingSearcher.instances[-1].calls


# -------------------------------------------------------- Branch D: empty pipe


def test_search_empty_pipe_returns_no_results(mfs_home, monkeypatch):
    """Empty stdin pipe must NOT trigger an indexed-corpus search."""
    _install_stubs(monkeypatch)
    runner = CliRunner()
    result = runner.invoke(main, ["search", "anything"], input="")
    assert result.exit_code == 0, result.output
    assert "No results." in result.output
    assert _SpyingSearcher.instances == []


def test_search_empty_pipe_json_returns_empty_list(mfs_home, monkeypatch):
    """With --json, an empty pipe emits `[]` (not a populated hit list)."""
    _install_stubs(monkeypatch)
    runner = CliRunner()
    result = runner.invoke(main, ["search", "anything", "--json"], input="")
    assert result.exit_code == 0, result.output
    # Trailing lines may include a one-time "note: created default config…"
    # on stderr; assert the JSON body appears on its own line.
    assert "[]" in result.output.splitlines()
    assert _SpyingSearcher.instances == []


# -------------------------------------------------------- Branch C: plain text pipe


def test_search_plain_text_pipe_searches_stdin_only(mfs_home, monkeypatch):
    """Non-empty stdin without ``::mfs:`` headers searches stdin, not corpus."""
    _install_stubs(monkeypatch)
    runner = CliRunner()
    result = runner.invoke(
        main, ["search", "fix"],
        input="commit abc123\nsome git log output\n",
    )
    assert result.exit_code == 0, result.output
    assert "<stdin>" in result.output
    assert "commit abc123" in result.output
    assert "without ::mfs: headers" not in result.output
    assert _SpyingSearcher.instances == []


# -------------------------------------------------------- Branch B: headered pipe


def test_search_headered_pipe_scopes_to_source(mfs_home, monkeypatch):
    """``::mfs:source=...`` headers in stdin still filter the corpus."""
    _install_stubs(monkeypatch)
    payload = (
        "::mfs:source=/tmp/docs/auth.md\n"
        "::mfs:indexed=true\n"
        "::mfs:hash=deadbeef\n"
        "\n"
        "# Auth\n\n body text\n"
    )
    runner = CliRunner()
    result = runner.invoke(main, ["search", "oauth"], input=payload)
    assert result.exit_code == 0, result.output
    calls = _calls()
    assert len(calls) == 1, f"expected exactly one search call, got {calls}"
    assert calls[0]["path_filter"] == "/tmp/docs/auth.md"


# -------------------------------------------------------- Branch A: tty / no pipe


def test_search_without_pipe_without_path_errors(mfs_home, monkeypatch, tmp_path):
    """POSIX semantics: tty stdin + no path + no --all → explicit error.

    The old behavior silently defaulted to cwd, which killed pipelines run
    from tmp dirs outside the indexed corpus. Now the caller must opt in to
    a scope.
    """
    _install_stubs(monkeypatch)
    monkeypatch.setattr(cli_mod, "stdin_has_data", lambda: False)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(main, ["search", "oauth"])
    assert result.exit_code == 2, result.output
    combined = result.output + (result.stderr or "")
    assert "no path specified" in combined.lower()
    # Searcher must NOT have been invoked.
    assert _SpyingSearcher.instances == []


def test_search_with_positional_path_scopes_to_path(mfs_home, monkeypatch, tmp_path):
    """Positional path argument scopes the search (matches grep convention)."""
    _install_stubs(monkeypatch)
    monkeypatch.setattr(cli_mod, "stdin_has_data", lambda: False)

    runner = CliRunner()
    result = runner.invoke(main, ["search", "oauth", str(tmp_path)])
    assert result.exit_code == 0, result.output
    calls = _calls()
    assert len(calls) == 1
    assert calls[0]["path_filter"] == str(tmp_path.resolve())


def test_search_path_option_still_works(mfs_home, monkeypatch, tmp_path):
    """``--path X`` is kept as an alias for backward compat."""
    _install_stubs(monkeypatch)
    monkeypatch.setattr(cli_mod, "stdin_has_data", lambda: False)

    runner = CliRunner()
    result = runner.invoke(main, ["search", "oauth", "--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    calls = _calls()
    assert len(calls) == 1
    assert calls[0]["path_filter"] == str(tmp_path.resolve())


def test_search_tty_with_all_flag_unfilters(mfs_home, monkeypatch):
    """``--all`` still bypasses the cwd filter when stdin isn't piped."""
    _install_stubs(monkeypatch)
    monkeypatch.setattr(cli_mod, "stdin_has_data", lambda: False)

    runner = CliRunner()
    result = runner.invoke(main, ["search", "oauth", "--all"])
    assert result.exit_code == 0, result.output
    calls = _calls()
    assert len(calls) == 1
    assert calls[0]["path_filter"] is None


# ----------------------------------------- stdin_has_data FD heuristic


def test_stdin_has_data_returns_false_for_character_device(monkeypatch):
    """A non-tty character-device stdin (e.g. /dev/null, Bash-tool subprocess)
    must NOT be treated as a pipe. Otherwise ``mfs search`` run under a
    subprocess with an inherited-closed stdin short-circuits to "No results"
    — the exact regression shipped by 2026-04-21.
    """
    from mfs.output import pipe as pipe_mod

    # /dev/null is a character device on Linux/macOS.
    with open("/dev/null", "r") as fh:
        monkeypatch.setattr(pipe_mod.sys, "stdin", fh)
        assert pipe_mod.stdin_has_data() is False


def test_stdin_has_data_returns_true_for_fifo(monkeypatch, tmp_path):
    """A real pipe (FIFO) is the case the four-branch logic was designed for."""
    import os as _os
    from mfs.output import pipe as pipe_mod

    fifo_path = tmp_path / "fifo"
    _os.mkfifo(str(fifo_path))
    # Open non-blocking read + a dummy writer so the FIFO is actually a FIFO.
    write_fd = _os.open(str(fifo_path), _os.O_RDWR)
    read_fh = open(str(fifo_path), "r")
    try:
        monkeypatch.setattr(pipe_mod.sys, "stdin", read_fh)
        assert pipe_mod.stdin_has_data() is True
    finally:
        read_fh.close()
        _os.close(write_fd)


def test_stdin_has_data_returns_true_for_regular_file(monkeypatch, tmp_path):
    """``mfs search < file.txt`` must flow through the pipe branches."""
    from mfs.output import pipe as pipe_mod

    f = tmp_path / "redirect.txt"
    f.write_text("hi\n", encoding="utf-8")
    with open(f, "r") as fh:
        monkeypatch.setattr(pipe_mod.sys, "stdin", fh)
        assert pipe_mod.stdin_has_data() is True


# ----------------------------------- end-to-end: search returns indexed results


def test_search_e2e_returns_results_after_add(mfs_home, tmp_path, monkeypatch):
    """Regression guard: after ``mfs add`` + ``mfs search`` under a non-tty
    stdin (CliRunner, CI, Bash tool) the indexed content must actually be
    searchable. Previously ``stdin_has_data``'s ``not isatty()`` heuristic
    silently short-circuited with "No results." in every non-terminal env.

    This test stubs the embedder and Searcher so it doesn't require a live
    backend — the point is to pin the CLI plumbing (stdin handling +
    path_filter construction + format_search_results) end-to-end.
    """
    _install_stubs(monkeypatch)
    # Pretend there's no real pipe. That's the same thing the new
    # stdin_has_data does for an inherited-closed stdin, and what a human
    # sees in a real terminal.
    monkeypatch.setattr(cli_mod, "stdin_has_data", lambda: False)

    # Return a real-looking result so format_search_results has something
    # to render.
    from mfs.store import SearchResult

    def _fake_search(self, query, mode, path_filter, top_k):
        self.calls.append({"query": query, "mode": mode,
                           "path_filter": path_filter, "top_k": top_k})
        return [
            SearchResult(
                source=str(tmp_path / "doc.md"),
                chunk_text="# OpenViking\n\nbody",
                chunk_index=0,
                start_line=1,
                end_line=3,
                content_type="markdown",
                score=0.42,
                is_dir=False,
                metadata={},
            )
        ]

    monkeypatch.setattr(_SpyingSearcher, "search", _fake_search)
    runner = CliRunner()
    result = runner.invoke(main, ["search", "OpenViking", "--all", "--top-k", "2"])
    assert result.exit_code == 0, result.output
    # The result must render (not be short-circuited to "No results.").
    assert "[1]" in result.output
    # Line range now lives in the body gutter (right-aligned start_line=1)
    # instead of the header's "L1-3" tag.
    assert "  1  " in result.output, result.output
    assert "OpenViking" in result.output
    # And --json must produce a hit, not `[]`.
    result2 = runner.invoke(
        main, ["search", "OpenViking", "--all", "--top-k", "2", "--json"]
    )
    assert result2.exit_code == 0
    import json as _json
    # Strip any preamble (e.g. "note: created default config").
    body = result2.output[result2.output.index("["):]
    parsed = _json.loads(body[: body.rindex("]") + 1])
    assert parsed and parsed[0]["metadata"]["kind"] == "search"
