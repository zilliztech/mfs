"""Directory summary tests."""

from __future__ import annotations


from mfs.search.summary import (
    aggregate_dir_summary,
    extract_file_summary,
    sort_by_priority,
)


def test_extract_file_summary_markdown(tmp_path):
    f = tmp_path / "guide.md"
    f.write_text("# Auth Guide\n\nHow to authenticate.\n\n## OAuth2\n\nDetails.\n")
    summary = extract_file_summary(f)
    assert "Auth Guide" in summary


def test_extract_file_summary_text(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("First paragraph here.\n\nSecond paragraph.\n")
    summary = extract_file_summary(f)
    assert "First paragraph" in summary


def test_aggregate_dir_summary_caps_length(tmp_path):
    entries = [(f"file{i}.md", f"child summary {i}" * 20, False) for i in range(10)]
    ds = aggregate_dir_summary(tmp_path, entries, file_count=10, indexed_count=10, max_chars=200)
    assert len(ds.text) <= 200


def test_aggregate_dir_summary_metadata(tmp_path):
    ds = aggregate_dir_summary(
        tmp_path, [("a.md", "hi", False)], file_count=2, indexed_count=1,
    )
    assert ds.metadata == {"file_count": 2, "indexed_count": 1}


def test_sort_by_priority_readme_first(tmp_path):
    (tmp_path / "z.md").write_text("z")
    (tmp_path / "README.md").write_text("readme")
    (tmp_path / "other.md").write_text("other")
    entries = list(tmp_path.iterdir())
    sorted_entries = sort_by_priority(entries)
    assert sorted_entries[0].name == "README.md"


def test_extract_file_summary_skips_binary(tmp_path):
    f = tmp_path / "garbage.log"
    # Heavy control-char payload (mimics a binary log file with embedded NULs).
    f.write_bytes(b"\x00\x01\x02\x03binary\x00noise\x01more\x02\x03\x04\x05\x06\x07")
    assert extract_file_summary(f) == ""


def test_extract_file_summary_skips_null_bytes(tmp_path):
    f = tmp_path / "has_null.bin"
    f.write_bytes(b"printable text but \x00 has null bytes inside")
    assert extract_file_summary(f) == ""


def test_extract_file_summary_ok_for_text(tmp_path):
    f = tmp_path / "plain.txt"
    f.write_text("Line one of a note.\n\nLine two.\n")
    summary = extract_file_summary(f)
    assert "Line one" in summary
