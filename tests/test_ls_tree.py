"""Regression tests for ls/tree output on non-indexed directories.

Covers the bug where binary log files and other non-classifiable entries
would produce garbage summaries because density extraction treated them as
plain text.
"""

from __future__ import annotations


from click.testing import CliRunner

from mfs.cli import _ls_entries, _tree_entries, main
from mfs.config import Config
from mfs.ingest.scanner import Scanner


def test_ls_entries_skips_summary_for_non_indexed_files(tmp_path):
    (tmp_path / "doc.md").write_text("# Title\n\nintro paragraph.\n", encoding="utf-8")
    (tmp_path / "noise.log").write_bytes(b"\x00\x01\x02raw binary \x00 garbage")
    (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n\x1a\npretend-image")
    (tmp_path / "package-lock.json").write_text('{"lockfileVersion": 2}', encoding="utf-8")

    entries = _ls_entries(tmp_path)
    by_name = {e["name"]: e for e in entries}

    assert "Title" in by_name["doc.md"]["summary"]
    # Non-indexed files should have an empty summary (clean filename-only).
    assert by_name["noise.log"]["summary"] == ""
    assert by_name["image.png"]["summary"] == ""
    assert by_name["package-lock.json"]["summary"] == ""


def test_tree_entries_marks_non_classifiable_files(tmp_path):
    (tmp_path / "a.md").write_text("# Hi\n", encoding="utf-8")
    (tmp_path / "weird.bin").write_bytes(b"\x00\x00\x00\x00")

    scanner = Scanner(Config())
    tree = _tree_entries(tmp_path, max_depth=2, scanner=scanner)

    children_by_name = {c["name"]: c for c in tree["children"]}
    assert children_by_name["a.md"]["summarizable"] is True
    assert children_by_name["weird.bin"]["summarizable"] is False


def test_ls_cli_clean_output_on_unindexed_dir(tmp_path):
    """End-to-end: `mfs ls` on a dir with binary files must not print
    garbage from those files or emit any 'binary file matches' noise."""
    (tmp_path / "real.md").write_text("# Real\n\nhello world\n", encoding="utf-8")
    (tmp_path / "binary.log").write_bytes(b"\x00\x01\x02\x03rest of noise")

    runner = CliRunner()
    result = runner.invoke(main, ["ls", str(tmp_path)])

    assert result.exit_code == 0
    assert "binary.log" in result.output
    assert "real.md" in result.output
    assert "binary file matches" not in result.output


def test_tree_cli_clean_output_on_unindexed_dir(tmp_path):
    (tmp_path / "real.md").write_text("# Real\n\ncontent\n", encoding="utf-8")
    (tmp_path / "binary.log").write_bytes(b"\x00\x01\x02raw data")

    runner = CliRunner()
    result = runner.invoke(main, ["tree", str(tmp_path)])

    assert result.exit_code == 0
    assert "binary.log" in result.output
    for line in result.output.splitlines():
        if "binary.log" in line:
            # Bare filename is fine; a " — <summary>" dash would mean we
            # density-extracted the binary bytes.
            assert " — " not in line, f"Unexpected summary on binary file: {line!r}"
