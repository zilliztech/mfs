"""Regression tests for ls/tree density plumbing.

The fast path (no preset / --skim without overrides) reuses cached skim
summaries. Any explicit preset (--peek/--deep) or -W/-H/-D override must
re-extract from the file so the output actually reflects the requested
density — previously the CLI always returned the cached skim view.
"""

from __future__ import annotations

import re

import pytest
from click.testing import CliRunner

from mfs.cli import _ls_continuation_cap, _ls_entries, main
from mfs.output.display import format_ls
from mfs.search.density import resolve_density


_LONG_PARAGRAPH = (
    "This is a deliberately long intro paragraph that exists specifically so "
    "that W-override tests can observe whether the rendered summary widens "
    "beyond the skim default. It repeats the same idea several times with "
    "slight variations to keep flowing well past the default skim width of "
    "eighty characters, then past one hundred, then past two hundred, and "
    "still keeps going for another burst of prose about indexing files and "
    "semantic search so that at -W 500 we can still see more text than at "
    "-W 80, which is the entire point of this fixture."
)

MARKDOWN_BODY = f"""# Big Topic

{_LONG_PARAGRAPH}

## Section Alpha

Alpha talks about things that happen first, in enough detail to be measurable.

## Section Beta

Beta continues the discussion with more concrete examples of the topic.

### Subsection Beta.1

Nested detail content that only deep-depth rendering should surface.

## Section Gamma

Gamma wraps things up and references future work.
"""


@pytest.fixture
def docs_dir(tmp_path):
    d = tmp_path / "docs"
    d.mkdir()
    (d / "guide.md").write_text(MARKDOWN_BODY, encoding="utf-8")
    return d


def _run_ls(mfs_home, docs_dir, *args):
    runner = CliRunner()
    result = runner.invoke(main, ["ls", *args, str(docs_dir)])
    assert result.exit_code == 0, result.output
    return result.output


# --------------------------------------------------------------------- ls


def test_ls_deep_produces_longer_output_than_skim(mfs_home, docs_dir):
    skim = _run_ls(mfs_home, docs_dir, "--skim")
    deep = _run_ls(mfs_home, docs_dir, "--deep")
    assert len(deep) > len(skim) * 1.5, (
        f"deep ({len(deep)} chars) should be noticeably longer than "
        f"skim ({len(skim)} chars):\nSKIM:\n{skim}\nDEEP:\n{deep}"
    )
    # deep must surface level-3 headings; skim (D=2) must not.
    assert "Subsection Beta.1" in deep
    assert "Subsection Beta.1" not in skim


def test_ls_w_override_widens_body_text(mfs_home, docs_dir):
    baseline = _run_ls(mfs_home, docs_dir, "--skim")
    wide = _run_ls(mfs_home, docs_dir, "-W", "500")
    # -W 500 should produce at least one body line noticeably wider than the
    # skim default (W=80 for markdown).
    max_baseline = max((len(line) for line in baseline.splitlines()), default=0)
    max_wide = max((len(line) for line in wide.splitlines()), default=0)
    assert max_wide > max_baseline + 40, (
        f"expected -W 500 to widen some line beyond baseline "
        f"(baseline max {max_baseline}, wide max {max_wide})"
    )
    # The wide variant should contain tail text that skim truncated out.
    assert "another burst of prose" in wide
    assert "another burst of prose" not in baseline


def test_ls_h1_override_shows_single_heading(mfs_home, docs_dir):
    output = _run_ls(mfs_home, docs_dir, "-H", "1")
    # With H=1, the density extraction emits a single heading + ellipsis note.
    # The display cap mirrors that — no extra continuation lines.
    guide_lines = [ln for ln in output.splitlines() if "guide.md" in ln]
    assert guide_lines, output
    # Exactly one guide.md header line, with no indented continuation bodies
    # following immediately underneath describing other sections.
    idx = output.splitlines().index(guide_lines[0])
    tail = output.splitlines()[idx + 1 :]
    # Only one continuation line at most (density emits "... (N more)") — but
    # certainly not the full multi-section skim tree.
    nested_headings = [ln for ln in tail if re.search(r"##\s+Section", ln)]
    assert len(nested_headings) == 0, f"unexpected sections in tail: {tail}"


def test_ls_peek_shows_only_names(mfs_home, docs_dir):
    output = _run_ls(mfs_home, docs_dir, "--peek")
    lines = [ln for ln in output.splitlines() if ln.strip()]
    # Header + one filename line, and nothing else.
    assert lines[0].endswith("/")
    assert "guide.md" in lines[1]
    assert not any("#" in ln or "Section" in ln for ln in lines[1:])


# --------------------------------------------------------------- _ls_entries


def test_ls_entries_use_cached_for_default_skim(mfs_home, docs_dir):
    """Default / skim with no overrides must reuse the rule-based skim view."""
    entries = _ls_entries(docs_dir, "skim")
    by_name = {e["name"]: e for e in entries}
    assert "# Big Topic" in by_name["guide.md"]["summary"]
    # Skim shouldn't expose Subsection Beta.1 (D=2).
    assert "Subsection Beta.1" not in by_name["guide.md"]["summary"]


def test_ls_entries_reextract_for_deep(mfs_home, docs_dir):
    entries = _ls_entries(docs_dir, "deep")
    by_name = {e["name"]: e for e in entries}
    summary = by_name["guide.md"]["summary"]
    # Deep (D=3) should include the nested level-3 heading.
    assert "Subsection Beta.1" in summary


def test_ls_entries_reextract_for_overrides(mfs_home, docs_dir):
    """Explicit -W / -H / -D must re-extract, not reuse cached skim."""
    cached = _ls_entries(docs_dir, "skim")
    overridden = _ls_entries(docs_dir, "skim", width=500)
    cached_sum = {e["name"]: e["summary"] for e in cached}["guide.md"]
    wide_sum = {e["name"]: e["summary"] for e in overridden}["guide.md"]
    # Cached skim truncates the long paragraph; the re-extracted view at
    # W=500 must preserve text that was cut out.
    assert "another burst of prose" in wide_sum
    assert "another burst of prose" not in cached_sum


# ------------------------------------------------ _ls_continuation_cap


def test_continuation_cap_rules():
    assert _ls_continuation_cap("peek", None) == 0
    assert _ls_continuation_cap("skim", None) == 2
    assert _ls_continuation_cap("deep", None) == 12
    # -H override drives the display cap for skim so -H 1 shows one line only.
    assert _ls_continuation_cap("skim", 1) == 0
    assert _ls_continuation_cap("skim", 5) == 4


# ------------------------------------------------------------- format_ls


def test_format_ls_respects_cont_cap():
    entries = [
        {
            "name": "foo.md",
            "is_dir": False,
            "path": "/tmp/foo.md",
            "indexed": True,
            "summary": "# Head\nbody1\nbody2\nbody3\nbody4\n",
        }
    ]
    params = resolve_density("directory", "skim")
    with_two = format_ls(None, entries, "skim", params, cont_cap=2)
    with_many = format_ls(None, entries, "deep", params, cont_cap=10)
    assert with_many.count("\n") > with_two.count("\n")


# ---------------------------------------------------------- mfs tree --deep


def test_tree_deep_output_differs_from_skim(mfs_home, docs_dir):
    runner = CliRunner()
    skim = runner.invoke(main, ["tree", "--skim", str(docs_dir)])
    deep = runner.invoke(main, ["tree", "--deep", str(docs_dir)])
    assert skim.exit_code == 0 and deep.exit_code == 0
    assert deep.output != skim.output, (
        f"tree --deep should produce richer output than --skim;\n"
        f"SKIM:\n{skim.output}\nDEEP:\n{deep.output}"
    )
    # Deep should embed at least one body line after the heading.
    assert any(
        "·" in line and "guide.md" in line for line in deep.output.splitlines()
    ), deep.output
