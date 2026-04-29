"""W/H/D density tests."""

from __future__ import annotations


from mfs.search.density import (
    DensityParams,
    detect_density_type,
    extract_csv_density,
    extract_density_view,
    extract_json_density,
    extract_jsonl_density,
    extract_markdown_density,
    extract_text_density,
    resolve_density,
)


def test_detect_density_type():
    assert detect_density_type(".md") == "markdown"
    assert detect_density_type(".py") == "code"
    assert detect_density_type(".json") == "json"
    assert detect_density_type(".jsonl") == "jsonl"
    assert detect_density_type(".csv") == "csv"
    assert detect_density_type(".txt") == "text"
    assert detect_density_type(".md", is_dir=True) == "directory"


def test_resolve_density_presets():
    p = resolve_density("markdown", "peek")
    assert p.w == 0 and p.h == 10 and p.d == 3

    p = resolve_density("markdown", "skim")
    assert p.w == 80 and p.h == 5 and p.d == 2


def test_resolve_density_overrides():
    p = resolve_density("markdown", "skim", w_override=250, h_override=20)
    assert p.w == 250 and p.h == 20 and p.d == 2


def test_extract_markdown_density_peek_shows_headings_only():
    md = "# Title\n\nintro para\n\n## Sub\n\nmore text\n\n### Deeper\n\ndetails\n"
    out = extract_markdown_density(md, w=0, h=10, d=3)
    assert "# Title" in out
    assert "## Sub" in out
    assert "### Deeper" in out
    # No content lines
    assert "intro para" not in out


def test_extract_markdown_density_respects_depth():
    md = "# A\n\n## B\n\n### C\n"
    out = extract_markdown_density(md, w=0, h=10, d=2)
    assert "# A" in out
    assert "## B" in out
    assert "### C" not in out


def test_extract_markdown_density_skips_code_fence_hashes():
    md = "# Real\n\n```bash\n# fake heading in code\n```\n\n## Sub\n"
    out = extract_markdown_density(md, w=0, h=10, d=3)
    assert "# Real" in out
    assert "## Sub" in out
    assert "# fake heading" not in out


def test_extract_text_density_paragraph_truncation():
    text = "one sentence about dogs.\n\nanother about cats.\n\nthird about birds.\n"
    out = extract_text_density(text, w=100, h=2)
    assert "one sentence" in out
    assert "another about" in out
    assert "third about" not in out


def test_extract_json_density_keys():
    content = '{"server": {"host": "127.0.0.1", "port": 8080}, "debug": true}'
    out = extract_json_density(content, w=0, h=5, d=1)
    assert "server:" in out or "server: {" in out
    assert "debug:" in out


def test_extract_jsonl_density():
    content = '{"a":1}\n{"a":2}\n{"a":3}\n'
    out = extract_jsonl_density(content, w=80, h=2)
    assert '{"a":1}' in out
    assert '{"a":2}' in out
    assert "1 more" in out


def test_extract_csv_density():
    content = "id,name,role\n1,alice,admin\n2,bob,user\n"
    out = extract_csv_density(content, w=30, h=2)
    assert "id" in out and "name" in out
    assert "alice" in out
    assert "bob" not in out
    assert "1 more" in out


def test_extract_density_view_dispatch():
    md = "# H\n\nhello\n"
    view = extract_density_view(md, "markdown", DensityParams(w=80, h=3, d=2))
    assert "# H" in view
