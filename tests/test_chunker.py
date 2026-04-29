"""Chunker tests."""

from __future__ import annotations

from mfs.ingest.chunker import (
    chunk_code,
    chunk_file,
    chunk_markdown,
    chunk_text,
    extract_frontmatter,
    generate_chunk_id,
)


def test_generate_chunk_id_is_deterministic():
    a = generate_chunk_id("/f.md", 1, 10, "abc", "model1")
    b = generate_chunk_id("/f.md", 1, 10, "abc", "model1")
    assert a == b
    assert len(a) == 16


def test_generate_chunk_id_changes_with_model():
    a = generate_chunk_id("/f.md", 1, 10, "abc", "model1")
    b = generate_chunk_id("/f.md", 1, 10, "abc", "model2")
    assert a != b


def test_extract_frontmatter_present():
    content = "---\ntitle: X\ntags: [a, b]\n---\n# Hello\n"
    fm, body, offset = extract_frontmatter(content)
    assert fm == {"title": "X", "tags": ["a", "b"]}
    assert body.startswith("# Hello")
    assert offset > 0


def test_extract_frontmatter_absent():
    content = "# Hello\n"
    fm, body, offset = extract_frontmatter(content)
    assert fm is None
    assert body == content
    assert offset == 0


def test_chunk_markdown_by_headings():
    md = "# Title\n\npara1\n\n## Sub\n\npara2\n\n## Sub2\n\npara3\n"
    chunks = chunk_markdown(md)
    assert len(chunks) == 3
    assert chunks[0].metadata["heading_level"] == 1
    assert chunks[0].metadata["heading_text"] == "Title"
    assert chunks[0].chunk_index == 0
    assert chunks[1].metadata["heading_text"] == "Sub"


def test_chunk_markdown_handles_frontmatter():
    md = "---\ntitle: T\n---\n# Hello\n\npara\n"
    chunks = chunk_markdown(md)
    assert len(chunks) == 1
    assert "title: T" in chunks[0].text


def test_chunk_markdown_no_headings_falls_back_to_text():
    md = "just some prose\n\nanother paragraph\n"
    chunks = chunk_markdown(md)
    assert chunks
    assert chunks[0].content_type == "markdown"


def test_chunk_text_paragraphs():
    text = "para1\n\npara2\n\npara3\n"
    chunks = chunk_text(text)
    assert len(chunks) == 1
    assert chunks[0].content_type == "text"
    assert chunks[0].start_line == 1


def test_chunk_code_splits_by_top_level_defs():
    code = (
        "import os\n\n"
        "def foo():\n    return 1\n\n"
        "class Bar:\n    def baz(self):\n        return 2\n"
    )
    chunks = chunk_code(code, ".py")
    assert len(chunks) >= 2
    names = {c.metadata.get("symbol_name") for c in chunks if c.metadata.get("symbol_name")}
    assert "foo" in names
    assert "Bar" in names


def test_chunk_file_routes_by_extension():
    md_chunks = chunk_file(None, "# Hi\n\nhello\n", ".md")
    assert md_chunks[0].content_type == "markdown"

    text_chunks = chunk_file(None, "some\n\ntext\n", ".txt")
    assert text_chunks[0].content_type == "text"
