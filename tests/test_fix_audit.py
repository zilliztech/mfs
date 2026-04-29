"""Regression tests for the audit fixes (F1-F3, W1/W2, W3-W7, W9-W10).

These tests pin the observable behavior so future refactors don't silently
reintroduce the issues caught in the 2026-04-20 end-to-end audit.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from mfs.cli import main
from mfs.cli_config import config_group
from mfs.config import MilvusConfig
from mfs.output.display import format_grep_results, format_status
from mfs.output.pipe import format_mfs_headers
from mfs.search.searcher import GrepMatch, Searcher, SearchMode
from mfs.store import ChunkRecord, MilvusStore


# ---------------------------------------------------------------- helpers


def _body(chunk_id: str, source: str, text: str = "hello") -> ChunkRecord:
    return ChunkRecord(
        id=chunk_id,
        source=source,
        parent_dir=str(Path(source).parent),
        chunk_index=0,
        start_line=1,
        end_line=3,
        chunk_text=text,
        dense_vector=[0.1] * 8,
        content_type="markdown",
        file_hash="h",
        is_dir=False,
        embed_status="complete",
        metadata={},
        account_id="default",
    )


def _dir(dir_path: str, text: str = "dir summary") -> ChunkRecord:
    return ChunkRecord(
        id="d" + dir_path.replace("/", "_")[:14],
        source=dir_path,
        parent_dir=str(Path(dir_path).parent),
        chunk_index=0,
        start_line=0,
        end_line=0,
        chunk_text=text,
        dense_vector=[0.0] * 8,
        content_type="directory",
        file_hash="",
        is_dir=True,
        embed_status="complete",
        metadata={},
        account_id="default",
    )


@pytest.fixture
def empty_store(tmp_path):
    cfg = MilvusConfig(uri=str(tmp_path / "empty_milvus.db"))
    s = MilvusStore(cfg, dimension=8)
    s.connect()
    return s


@pytest.fixture
def populated_store(tmp_path):
    cfg = MilvusConfig(uri=str(tmp_path / "populated_milvus.db"))
    s = MilvusStore(cfg, dimension=8)
    s.connect()
    s.insert_chunks([
        _body("a1", "/proj/a.md", "authentication and tokens"),
        _body("b1", "/proj/b.md", "deployment configuration"),
        _dir("/proj"),
    ])
    return s


class _StubEmbedder:
    model_name = "stub"
    dimension = 8

    def embed(self, texts):
        return [[0.1] * self.dimension for _ in texts]


# ------------------------------------------------------------------ F1


@pytest.mark.parametrize("mode", [SearchMode.HYBRID, SearchMode.SEMANTIC, SearchMode.KEYWORD])
def test_search_empty_index_returns_no_results(empty_store, mode):
    searcher = Searcher(empty_store, _StubEmbedder())
    results = searcher.search("anything", mode=mode)
    assert results == []


def test_store_is_empty_flag(empty_store, populated_store):
    assert empty_store.is_empty() is True
    assert populated_store.is_empty() is False


# ------------------------------------------------------------------ F2


def test_format_mfs_headers_omits_hash_for_unindexed():
    s = format_mfs_headers("/tmp/x.md", indexed=False, file_hash="")
    assert "::mfs:indexed=false" in s
    assert "::mfs:hash=" not in s


def test_format_mfs_headers_keeps_hash_for_indexed():
    s = format_mfs_headers("/tmp/x.md", indexed=True, file_hash="abcd1234")
    assert "::mfs:indexed=true" in s
    assert "::mfs:hash=abcd1234" in s


def test_cat_pipe_unindexed_file_reports_false(mfs_home, tmp_path):
    """Catting an un-indexed file through a pipe must not claim indexed=true."""
    target = tmp_path / "fresh.md"
    target.write_text("# Fresh\n\nunindexed content.\n", encoding="utf-8")

    runner = CliRunner()
    # --meta forces the header even though CliRunner's stdout isn't a pipe.
    result = runner.invoke(main, ["cat", "--meta", str(target)])
    assert result.exit_code == 0, result.output
    assert "::mfs:indexed=false" in result.output
    # Hash line must be omitted when not indexed.
    assert "::mfs:hash=" not in result.output


# ------------------------------------------------------------------ F3


def test_cat_refuses_binary_file(mfs_home, tmp_path):
    binary = tmp_path / "blob.bin"
    binary.write_bytes(b"\x7fELF\x02\x01\x00\x00" + b"\x00" * 512)
    runner = CliRunner()
    result = runner.invoke(main, ["cat", str(binary)])
    assert result.exit_code != 0
    # The raw ELF bytes must not appear on stdout.
    assert "\x7fELF" not in result.output


def test_cat_accepts_text_file(mfs_home, tmp_path):
    text = tmp_path / "notes.md"
    text.write_text("# Notes\n\nreadable.\n", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(main, ["cat", str(text)])
    assert result.exit_code == 0
    assert "readable" in result.output


# ------------------------------------------------------------ W1 / W2


def test_count_all_excludes_dir_summaries(populated_store):
    counts = populated_store.count_all()
    # Two body files; the dir record lives in `dir_summaries`.
    assert counts["files"] == 2
    assert counts["total_chunks"] == 2
    assert counts["dir_summaries"] == 1


def test_format_status_renders_dir_summary_line():
    out = format_status({
        "state": "idle", "files": 4, "total_chunks": 12, "complete_chunks": 12,
        "pending_chunks": 0, "dir_summaries": 2, "queue_size": 0,
        "processed": 0, "sync_times": {}, "worker_running": False,
    })
    assert "Indexed files: 4" in out
    assert "Directory summaries: 2" in out


# ------------------------------------------------------------------ W4


def test_tree_json_populates_summary(mfs_home, tmp_path):
    d = tmp_path / "docs"
    d.mkdir()
    (d / "guide.md").write_text("# Guide\n\nhello world body text.\n", encoding="utf-8")
    # Seed the config file so ensure_mfs_home() doesn't emit a stderr note
    # that would contaminate the captured JSON output.
    (mfs_home / "config.toml").write_text("", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(main, ["tree", "--json", str(d)])
    assert result.exit_code == 0, result.output
    # Unified Hit envelope: a flat list where each item has source/content/metadata.
    # Trim to the first `[` so stderr chatter (rich warnings) doesn't confuse the parse.
    out = result.output
    start = out.index("[")
    data = json.loads(out[start:])
    assert isinstance(data, list)
    guide = next(h for h in data if h["metadata"]["name"] == "guide.md")
    assert guide["metadata"]["kind"] == "tree"
    assert guide["content"], f"expected non-empty summary, got {guide!r}"
    assert "Guide" in guide["content"]


# ------------------------------------------------------------------ W5


def test_grep_dedupes_overlapping_context_lines():
    # Two matches on the same file; their context windows overlap on line 5.
    m1 = GrepMatch(
        source="/tmp/x.py",
        line_number=5,
        line_text="match A",
        context_before=["line 3", "line 4"],
        context_after=["line 6", "line 7"],
    )
    m2 = GrepMatch(
        source="/tmp/x.py",
        line_number=6,
        line_text="match B",
        context_before=["line 4", "match A"],
        context_after=["line 7", "line 8"],
    )
    import re
    out = format_grep_results([m1, m2])
    # Path appears once as a group header; gutter rows carry line numbers.
    lines = out.splitlines()
    assert sum(1 for ln in lines if ln.strip() == "/tmp/x.py") == 1, lines
    line_numbers: list[int] = []
    for ln in lines:
        m = re.match(r"^\s*(\d+) {2}", ln)
        if m:
            line_numbers.append(int(m.group(1)))
    assert line_numbers == sorted(line_numbers), f"not sorted: {line_numbers}"
    assert len(line_numbers) == len(set(line_numbers)), (
        f"duplicate line numbers in grep output: {line_numbers}"
    )


# ------------------------------------------------------------------ W6


def test_config_init_force_backs_up_existing(mfs_home):
    p = mfs_home / "config.toml"
    p.write_text("original = true\n", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(config_group, ["init", "--force"])
    assert result.exit_code == 0, result.output
    assert "backed up" in result.output
    # Exactly one backup file that starts with "config.toml.bak."
    backups = list(mfs_home.glob("config.toml.bak.*"))
    assert backups, f"expected backup file in {mfs_home}, found: {list(mfs_home.iterdir())}"
    assert "original = true" in backups[0].read_text(encoding="utf-8")


# ------------------------------------------------------------------ W7


def test_format_status_renders_human_timestamp():
    # Pick a fixed timestamp in 2026 so the rendered string is predictable.
    ts = 1776674009.16  # 2026-04-20-ish UTC
    out = format_status({
        "state": "idle", "files": 1, "total_chunks": 1, "complete_chunks": 1,
        "pending_chunks": 0, "dir_summaries": 0, "queue_size": 0,
        "processed": 0, "sync_times": {"/proj": ts}, "worker_running": False,
    })
    # The raw float must NOT appear; an ISO-8601-ish date must.
    assert "1776674009" not in out
    assert "2026-" in out


# ------------------------------------------------------------------ W9


def test_searches_unheadered_stdin_without_corpus_fallback(mfs_home, tmp_path, monkeypatch):
    """When stdin has text data without ::mfs: headers, search only that text.

    Unix-pipeline semantics: a caller who clearly piped something in doesn't
    want a silent fallback to "search the whole indexed corpus".
    """
    class _StubEmbedder:
        model_name = "stub"
        dimension = 8

        def embed(self, texts):
            return [[1.0] * 8 for _ in texts]

    monkeypatch.setattr("mfs.cli._build_embedder", lambda _cfg: _StubEmbedder())
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["search", "anything"],
        input="arbitrary stdin without headers\n",
    )
    combined = result.output + (result.stderr or "")
    assert "without ::mfs: headers" not in combined
    assert "<stdin>" in combined
    assert "arbitrary stdin without headers" in combined


# ----------------------------------------------------------------- W10


def test_format_status_handles_milvus_busy():
    out = format_status({
        "state": "indexing", "files": 0, "total_chunks": 0, "complete_chunks": 0,
        "pending_chunks": 0, "dir_summaries": 0, "queue_size": 3,
        "processed": 1, "sync_times": {}, "worker_running": True,
        "milvus_busy": True,
    })
    # No scary traceback-style warning; just a concise note.
    assert "Milvus busy" in out
    # Chunk counts hidden to avoid misleading zeros.
    assert "Chunks:" not in out
    # Queue state still surfaced.
    assert "Queue: 3 tasks waiting" in out
