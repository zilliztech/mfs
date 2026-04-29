"""Regression tests for Plan A output unification + cat line numbers.

Covers:
- Search output drops rich Panel box characters and indents chunk bodies.
- ``--json`` output for search / grep / ls / tree / cat shares a unified
  ``Hit`` envelope (same top-level keys, differentiated by metadata.kind).
- cat --peek/--skim/--deep prefix each density line with a right-aligned
  source line number; ``--no-line-numbers`` strips them.
- The Hit envelope itself round-trips through to_dict().
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from mfs.cli import main
from mfs.output.display import format_search_results, format_cat_density, format_cat_result
from mfs.output.schema import Hit
from mfs.search.density import resolve_density
from mfs.search.searcher import GrepMatch
from mfs.store import SearchResult


# ----------------------------------------------------- Change 1: no box chars


_BOX_CHARS = ("╭", "╮", "╰", "╯", "│", "─")


def _fake_result(idx: int) -> SearchResult:
    return SearchResult(
        source=f"/tmp/doc{idx}.md",
        chunk_text=f"# Heading {idx}\n\nbody line for result {idx}",
        chunk_index=0,
        start_line=1,
        end_line=3,
        content_type="markdown",
        score=0.5 - 0.01 * idx,
        is_dir=False,
        metadata={},
    )


def test_search_no_box_drawing():
    results = [_fake_result(1), _fake_result(2)]
    out = format_search_results(results)
    for ch in _BOX_CHARS:
        assert ch not in out, (
            f"search output should not contain box char {ch!r}; got:\n{out}"
        )


def test_search_body_uses_gutter_line_numbers():
    results = [_fake_result(1)]
    out = format_search_results(results)
    # Body lines should carry a right-aligned line-number gutter that
    # matches the chunk's start_line / end_line range (1..3 here).
    lines = out.splitlines()
    body_lines = [ln for ln in lines if ln.strip() and not ln.startswith("[")]
    assert body_lines, out
    # Width three (max(3, log10(end_line))) → "  1", "  2", "  3".
    assert body_lines[0].startswith("  1  "), body_lines[0]
    # Every body line begins with the 3-digit gutter + 2 spaces.
    for ln in body_lines:
        assert len(ln) >= 5 and ln[:3].strip().isdigit(), f"bad gutter: {ln!r}"
        assert ln[3:5] == "  ", f"bad separator: {ln!r}"


def test_search_header_shows_score_not_line_range():
    results = [_fake_result(1)]
    out = format_search_results(results)
    header = out.splitlines()[0]
    # Line range lives in the gutter now; header stays thin.
    assert "L1-3" not in header, header
    assert "score=0.490" in header  # three-decimal rendering from spec


def test_search_summary_chunk_keeps_summary_tag_and_indents():
    summary = SearchResult(
        source="/tmp/doc.md",
        chunk_text="auto LLM summary text",
        chunk_index=-1,
        start_line=0,
        end_line=0,
        content_type="llm_summary",
        score=0.42,
        is_dir=False,
        metadata={},
    )
    out = format_search_results([summary])
    lines = out.splitlines()
    header = lines[0]
    assert "[summary]" in header
    assert "L0" not in header
    # Summary chunks fall back to plain 4-space indent (no gutter).
    body = [ln for ln in lines if "auto LLM summary text" in ln]
    assert body and body[0].startswith("    "), body


# ------------------------------------------------- Change 3: Hit envelope


_HIT_KEYS = {"source", "lines", "content", "score", "metadata"}


def test_hit_envelope_serializes_tuple_lines_as_list():
    h = Hit(source="/x", lines=(1, 4), content="x", score=1.0,
            metadata={"kind": "search"})
    d = h.to_dict()
    assert d["lines"] == [1, 4]
    assert set(d) == _HIT_KEYS


def test_hit_envelope_handles_no_lines():
    h = Hit(source="/x", lines=None, content="x", metadata={"kind": "ls"})
    d = h.to_dict()
    assert d["lines"] is None
    assert d["score"] is None


def test_search_json_uses_hit_envelope():
    out = format_search_results([_fake_result(1)], output_json=True)
    data = json.loads(out)
    assert isinstance(data, list)
    assert set(data[0]) == _HIT_KEYS
    assert data[0]["metadata"]["kind"] == "search"
    assert data[0]["metadata"]["content_type"] == "markdown"
    assert data[0]["lines"] == [1, 3]


def test_grep_json_uses_hit_envelope():
    from mfs.output.display import format_grep_results

    m = GrepMatch(source="/tmp/x.md", line_number=7, line_text="hit",
                  context_before=["before"], context_after=["after"])
    out = format_grep_results([m], output_json=True)
    data = json.loads(out)
    assert set(data[0]) == _HIT_KEYS
    assert data[0]["metadata"]["kind"] == "grep"
    assert data[0]["lines"] == [7, 7]
    assert data[0]["metadata"]["context_before"] == ["before"]
    assert data[0]["metadata"]["context_after"] == ["after"]


def test_cat_json_uses_hit_envelope():
    out = format_cat_result(
        "/tmp/guide.md",
        "# Guide\n\nbody",
        content_type="markdown",
        lines=(1, 3),
        indexed=True,
        file_hash="abc123",
        preset=None,
    )
    data = json.loads(out)
    assert isinstance(data, list)
    assert set(data[0]) == _HIT_KEYS
    assert data[0]["metadata"]["kind"] == "cat"
    assert data[0]["metadata"]["content_type"] == "markdown"
    assert data[0]["metadata"]["indexed"] is True
    assert data[0]["lines"] == [1, 3]


def test_ls_json_uses_hit_envelope():
    from mfs.output.display import format_ls
    from pathlib import Path

    entries = [
        {
            "name": "guide.md",
            "is_dir": False,
            "path": "/tmp/docs/guide.md",
            "indexed": True,
            "summary": "# Guide",
            "stale": False,
        },
        {
            "name": "sub",
            "is_dir": True,
            "path": "/tmp/docs/sub",
            "indexed": False,
            "summary": "",
        },
    ]
    params = resolve_density("directory", "skim")
    out = format_ls(Path("/tmp/docs"), entries, "skim", params, output_json=True)
    data = json.loads(out)
    assert isinstance(data, list)
    for hit in data:
        assert set(hit) == _HIT_KEYS
        assert hit["metadata"]["kind"] == "ls"


def test_tree_json_uses_hit_envelope_with_depth(mfs_home, tmp_path):
    d = tmp_path / "docs"
    d.mkdir()
    (d / "a.md").write_text("# A\n\nhello.\n", encoding="utf-8")
    sub = d / "sub"
    sub.mkdir()
    (sub / "b.md").write_text("# B\n\nworld.\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(main, ["tree", "--json", str(d)])
    assert result.exit_code == 0, result.output
    start = result.output.index("[")
    end = result.output.rindex("]") + 1
    data = json.loads(result.output[start:end])
    assert isinstance(data, list)
    # Root at depth 0, children at 1, grandchildren at 2.
    depths = {h["metadata"]["depth"] for h in data}
    assert {0, 1, 2}.issubset(depths), depths
    # Every entry shares the Hit keys.
    for hit in data:
        assert set(hit) == _HIT_KEYS
        assert hit["metadata"]["kind"] == "tree"


def test_unified_hit_json_shape_across_commands(mfs_home, sample_project):
    """search/grep/ls/tree/cat --json all produce the same top-level envelope."""
    from mfs.output.display import format_grep_results, format_ls
    from pathlib import Path

    # search envelope
    search_out = format_search_results([_fake_result(1)], output_json=True)

    # grep envelope
    grep_out = format_grep_results(
        [GrepMatch(source="/tmp/x.md", line_number=1, line_text="hi",
                   context_before=[], context_after=[])],
        output_json=True,
    )

    # ls envelope
    params = resolve_density("directory", "skim")
    ls_out = format_ls(
        Path("/tmp"),
        [{"name": "f.md", "is_dir": False, "path": "/tmp/f.md",
          "indexed": True, "summary": "x"}],
        "skim", params, output_json=True,
    )

    cat_out = format_cat_result(
        "/tmp/f.md",
        "hi",
        content_type="markdown",
        lines=(1, 1),
        indexed=False,
    )

    for out in (search_out, grep_out, ls_out, cat_out):
        parsed = json.loads(out)
        assert isinstance(parsed, list) and parsed, out
        assert set(parsed[0]) == _HIT_KEYS, out


# ------------------------------------- Change 4: cat --peek line numbers


_MD_FIXTURE = (
    "# Top\n"           # line 1
    "\n"                 # line 2
    "intro paragraph\n"  # line 3
    "\n"                 # line 4
    "## Middle\n"        # line 5
    "\n"                 # line 6
    "more text here\n"   # line 7
    "\n"                 # line 8
    "## Bottom\n"        # line 9
    "\n"
    "final words\n"
)


def test_format_cat_density_adds_right_aligned_line_numbers():
    params = resolve_density("markdown", "peek")
    out = format_cat_density(_MD_FIXTURE, "markdown", params, total_lines=40)
    lines = out.splitlines()
    # Heading at source line 1 should be rendered as "  1  # Top"
    assert any("# Top" in ln for ln in lines)
    top = next(ln for ln in lines if "# Top" in ln)
    # Default width = max(3, log10) -> width 3 for 40 lines => "  1"
    assert top.startswith("  1  "), top


def test_format_cat_density_no_line_numbers_strips_prefix():
    params = resolve_density("markdown", "peek")
    out = format_cat_density(_MD_FIXTURE, "markdown", params, show_line_numbers=False)
    assert "# Top" in out
    first = out.splitlines()[0]
    # No leading whitespace+digits prefix.
    assert not first.startswith(" "), first
    assert first.startswith("# Top")


def test_cat_peek_cli_includes_line_numbers(mfs_home, tmp_path):
    f = tmp_path / "guide.md"
    f.write_text(_MD_FIXTURE, encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(main, ["cat", "--peek", "--no-meta", str(f)])
    assert result.exit_code == 0, result.output
    # The first heading is on source line 1; look for the "  1  # Top" prefix.
    assert "  1  # Top" in result.output, result.output


def test_cat_no_line_numbers_flag(mfs_home, tmp_path):
    f = tmp_path / "guide.md"
    f.write_text(_MD_FIXTURE, encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        main, ["cat", "--peek", "--no-line-numbers", "--no-meta", str(f)]
    )
    assert result.exit_code == 0, result.output
    # Heading must be flush-left with no numeric prefix.
    out_lines = [ln for ln in result.output.splitlines() if ln.strip()]
    assert out_lines[0].startswith("# Top"), out_lines


def test_cat_skim_shows_line_numbers_for_body_paragraph(mfs_home, tmp_path):
    f = tmp_path / "guide.md"
    f.write_text(_MD_FIXTURE, encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(main, ["cat", "--skim", "--no-meta", str(f)])
    assert result.exit_code == 0, result.output
    # Body paragraph is on source line 3.
    assert "  3  " in result.output, result.output


def test_cat_json_cli(mfs_home, tmp_path):
    f = tmp_path / "guide.md"
    f.write_text(_MD_FIXTURE, encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(main, ["cat", "--json", "-n", "1:3", str(f)])
    assert result.exit_code == 0, result.output
    start = result.output.index("[")
    end = result.output.rindex("]") + 1
    data = json.loads(result.output[start:end])
    assert data[0]["metadata"]["kind"] == "cat"
    assert data[0]["source"] == str(f.resolve())
    assert data[0]["lines"] == [1, 3]
    assert "# Top" in data[0]["content"]


# --------------------------------- left-gutter unification across search/grep


def test_search_uses_left_gutter_line_numbers():
    """Search body must render with a right-aligned gutter starting at
    ``start_line``, matching cat's visual skeleton so all three commands read
    the same way."""
    r = SearchResult(
        source="/tmp/doc.md",
        chunk_text="first line\nsecond line\nthird line",
        chunk_index=0,
        start_line=5,
        end_line=7,
        content_type="markdown",
        score=0.3,
        is_dir=False,
        metadata={},
    )
    out = format_search_results([r])
    lines = out.splitlines()
    # Header carries the score but not the legacy "L5-7" tag.
    assert "score=0.300" in lines[0]
    assert "L5-7" not in lines[0]
    # Body begins at start_line=5 and numbers increment per rendered line.
    body = [ln for ln in lines if ln.strip() and not ln.startswith("[")]
    assert body[0].startswith("  5  first line"), body[0]
    assert body[1].startswith("  6  second line"), body[1]
    assert body[2].startswith("  7  third line"), body[2]


def test_grep_groups_by_file_with_gutter():
    """Grep must print the path once per file, then gutter rows (matches
    rendered bright, context dim) until the next file group."""
    from mfs.output.display import format_grep_results
    from mfs.search.searcher import GrepMatch

    m_a = GrepMatch(source="/tmp/a.md", line_number=3, line_text="alpha match",
                    context_before=["alpha ctx1", "alpha ctx2"],
                    context_after=[])
    m_b = GrepMatch(source="/tmp/b.md", line_number=10, line_text="beta match",
                    context_before=[], context_after=["beta after"])
    out = format_grep_results([m_a, m_b])
    lines = out.splitlines()

    # Each file path appears exactly once as a standalone header row.
    assert lines.count("/tmp/a.md") == 1
    assert lines.count("/tmp/b.md") == 1

    # Context before the match uses the gutter too (lines 1, 2 for a.md)
    # and a match row at line 3.
    import re as _re

    def find_num(num: int) -> str:
        pattern = _re.compile(rf"^\s*{num} {{2}}")
        for ln in lines:
            if pattern.match(ln):
                return ln
        raise AssertionError(f"line {num} not found in:\n{out}")

    assert find_num(1).endswith("alpha ctx1")
    assert find_num(2).endswith("alpha ctx2")
    assert find_num(3).endswith("alpha match")
    assert find_num(10).endswith("beta match")
    assert find_num(11).endswith("beta after")

    # A blank line separates the two groups.
    a_idx = lines.index("/tmp/a.md")
    b_idx = lines.index("/tmp/b.md")
    assert "" in lines[a_idx + 1:b_idx]


def test_grep_dropped_path_line_content_format():
    """The legacy ``path:line:content`` per-line shape is gone."""
    from mfs.output.display import format_grep_results
    from mfs.search.searcher import GrepMatch

    m = GrepMatch(source="/tmp/x.md", line_number=1, line_text="hello",
                  context_before=[], context_after=[])
    out = format_grep_results([m])
    # No ``path:line:content`` rows — path only appears once as a header.
    assert "/tmp/x.md:1:hello" not in out
    assert out.count("/tmp/x.md") == 1
