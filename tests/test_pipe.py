"""Pipe header parsing tests."""

from __future__ import annotations

from mfs.output.pipe import format_mfs_headers, parse_mfs_headers


def test_format_mfs_headers_basic():
    s = format_mfs_headers("/tmp/a.md", True, "abcd1234")
    assert s.startswith("::mfs:source=/tmp/a.md\n")
    assert "::mfs:indexed=true" in s
    assert "::mfs:hash=abcd1234" in s
    assert s.endswith("\n\n")


def test_format_mfs_headers_with_lines():
    s = format_mfs_headers("/tmp/a.md", True, "h", lines="1:10")
    assert "::mfs:lines=1:10" in s


def test_parse_mfs_headers_present():
    text = (
        "::mfs:source=/tmp/x.md\n"
        "::mfs:indexed=true\n"
        "::mfs:hash=deadbeef\n"
        "\n"
        "# Body content\n"
        "more body\n"
    )
    headers, body = parse_mfs_headers(text)
    assert headers is not None
    assert headers["source"] == "/tmp/x.md"
    assert headers["indexed"] == "true"
    assert headers["hash"] == "deadbeef"
    assert body.startswith("# Body content")


def test_parse_mfs_headers_absent():
    text = "plain body without headers\n"
    headers, body = parse_mfs_headers(text)
    assert headers is None
    assert body == text
