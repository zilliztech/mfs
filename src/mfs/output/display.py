"""Output formatting for MFS commands.

All hit-list commands (search/grep/ls/tree) share the :class:`Hit` JSON
envelope so agents and downstream tools can parse any of them with one
schema. Text output follows a unified visual skeleton — a right-aligned
line-number gutter followed by two spaces of content — so search, grep,
and cat read as the same shape.

Colors are centralized in :mod:`mfs.output.colors` — do not hardcode new
``style="cyan"`` strings in this module.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from pathlib import Path

from rich.console import Console
from rich.text import Text

from ..search.density import (
    DensityParams,
    density_view_for_path,
    extract_density_view_with_lines,
)
from ..search.searcher import GrepMatch
from ..store import SearchResult
from . import colors as Color
from .schema import Hit


def is_pipe() -> bool:
    return not sys.stdout.isatty()


def _console() -> Console:
    # Respect is_pipe (no color) when stdout is piped
    return Console(force_terminal=None, highlight=False)


def _dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


# ----------------------------------------------------- shared gutter helper


def _gutter_width(max_line: int) -> int:
    """Minimum three-char gutter; grows for four-digit line numbers and up."""
    return max(3, len(str(max(1, max_line))))


def _format_gutter_line(
    line_no: int, text: str, gutter_width: int, *, dim: bool = False,
) -> Text:
    """Right-aligned line number + 2 spaces + content.

    Returns a :class:`rich.text.Text` so the caller can feed it straight to
    ``console.print``; rich strips color codes automatically in pipe mode,
    which keeps test and agent parsing trivial.
    """
    out = Text()
    num = str(line_no).rjust(gutter_width)
    if dim:
        out.append(num, style=Color.NOTE)
        out.append("  ")
        out.append(text, style=Color.NOTE)
    else:
        out.append(num, style=Color.LINE)
        out.append("  ")
        out.append(text)
    return out


# ---------------------------------------------------------------- search


def format_search_results(
    results: list[SearchResult],
    output_json: bool = False,
    quiet: bool = False,
) -> str:
    if output_json:
        hits = [
            Hit(
                source=r.source,
                lines=(
                    None
                    if r.chunk_index == -1
                    else (r.start_line, r.end_line)
                ),
                content=r.chunk_text,
                score=r.score,
                metadata={
                    "kind": "search",
                    "content_type": r.content_type,
                    "is_dir": r.is_dir,
                    "chunk_index": r.chunk_index,
                    **(r.metadata or {}),
                },
            ).to_dict()
            for r in results
        ]
        return _dumps(hits)

    if not results:
        return "No results."

    console = _console()
    with console.capture() as cap:
        for i, r in enumerate(results, 1):
            # Header: [N] <path>  score=X.XXX  [summary|dir]
            # Line range moved into the body's gutter; the header stays thin.
            header = Text()
            header.append(f"[{i}] ", style=Color.INDEX)
            header.append(r.source, style=Color.PATH)
            header.append(f"  score={r.score:.3f}", style=Color.SCORE)
            if r.chunk_index == -1:
                header.append("  [summary]", style=Color.META)
            if r.is_dir:
                header.append("  [dir]", style=Color.META)
            console.print(header)
            if not quiet:
                body = r.chunk_text.strip("\n")
                if len(body) > 400:
                    body = body[:400].rstrip() + "…"
                console.print("")
                body_lines = body.splitlines() or [""]
                if r.chunk_index == -1 or r.start_line <= 0:
                    # Summary / LLM chunks have no meaningful source lines;
                    # fall back to the old 4-space indent.
                    for body_line in body_lines:
                        console.print("    " + body_line)
                else:
                    last_line = r.end_line if r.end_line > 0 else (
                        r.start_line + len(body_lines) - 1
                    )
                    width = _gutter_width(last_line)
                    for offset, body_line in enumerate(body_lines):
                        console.print(
                            _format_gutter_line(
                                r.start_line + offset, body_line, width,
                            )
                        )
            # Blank line between results
            console.print("")
    return cap.get().rstrip()


# ---------------------------------------------------------------- grep


def format_grep_results(
    matches: list[GrepMatch],
    line_numbers: bool = False,  # deprecated: always on in gutter mode
    output_json: bool = False,
) -> str:
    if output_json:
        hits = [
            Hit(
                source=m.source,
                lines=(m.line_number, m.line_number),
                content=m.line_text,
                score=None,
                metadata={
                    "kind": "grep",
                    "context_before": list(m.context_before),
                    "context_after": list(m.context_after),
                },
            ).to_dict()
            for m in matches
        ]
        return _dumps(hits)

    if not matches:
        return "No matches."

    # Dedupe by (source, line_number). When the same line is both a match
    # and a context line (overlapping chunks), the match representation
    # wins so users still see their hit highlighted.
    seen: dict[tuple[str, int], tuple[str, bool]] = {}
    for m in matches:
        for i, ctx in enumerate(m.context_before):
            ln = m.line_number - len(m.context_before) + i
            key = (m.source, ln)
            if key not in seen:
                seen[key] = (ctx, False)
        key = (m.source, m.line_number)
        # A match beats any context line already recorded at this slot.
        seen[key] = (m.line_text, True)
        for i, ctx in enumerate(m.context_after):
            ln = m.line_number + 1 + i
            key = (m.source, ln)
            if key not in seen or not seen[key][1]:
                seen[key] = (ctx, False)

    # Group by source, preserving ascending line order within each group.
    by_source: dict[str, list[tuple[int, str, bool]]] = {}
    for (source, ln) in sorted(seen.keys()):
        text, is_match = seen[(source, ln)]
        by_source.setdefault(source, []).append((ln, text, is_match))

    cwd = Path.cwd()
    console = _console()
    with console.capture() as cap:
        first = True
        for source, entries in by_source.items():
            if not first:
                # Blank line separates file groups so multi-file output
                # stays readable.
                console.print("")
            first = False

            # Path header (cyan, printed once per file).
            display_path = _display_path(source, cwd)
            console.print(Text(display_path, style=Color.PATH))

            width = _gutter_width(max(ln for ln, _, _ in entries))
            prev_ln: int | None = None
            for (ln, text, is_match) in entries:
                if prev_ln is not None and ln > prev_ln + 1:
                    console.print("--")
                console.print(
                    _format_gutter_line(ln, text, width, dim=not is_match)
                )
                prev_ln = ln
    return cap.get().rstrip()


def _display_path(source: str, cwd: Path) -> str:
    """Render *source* relative to *cwd* when it sits under it, like ripgrep.

    Paths that escape upwards (``../``) keep their absolute form so users
    aren't misled about a file's location.
    """
    try:
        rel = os.path.relpath(source, cwd)
    except ValueError:
        return source
    if rel.startswith(".."):
        return source
    return rel


# ---------------------------------------------------------------- status


def format_status(status: dict[str, Any], output_json: bool = False) -> str:
    if output_json:
        return _dumps(status)

    lines = []
    state = status.get("state", "idle")
    total = int(status.get("total_chunks", 0))
    complete = int(status.get("complete_chunks", 0))
    pending = int(status.get("pending_chunks", 0))
    files = int(status.get("files", 0))
    dir_summaries = int(status.get("dir_summaries", 0))
    queue_size = int(status.get("queue_size", 0))
    processed = int(status.get("processed", 0))

    lines.append(f"State: {state}")
    if status.get("milvus_busy"):
        # Milvus Lite is single-writer; during `mfs add` the worker holds
        # the write lock and a concurrent `mfs status` can't connect. Show
        # queue-level state in that case and skip the chunk counts.
        lines.append("Milvus busy (worker writing); showing queue state only.")
    else:
        lines.append(f"Indexed files: {files}")
        lines.append(f"Chunks: {total} total  ({complete} complete, {pending} pending)")
        if dir_summaries:
            lines.append(f"Directory summaries: {dir_summaries}")
    if queue_size:
        lines.append(f"Queue: {queue_size} tasks waiting")
    if processed:
        lines.append(f"Processed this session: {processed}")
    sync_times = status.get("sync_times") or {}
    if sync_times:
        lines.append("Last sync:")
        for k, v in sync_times.items():
            lines.append(f"  {k}: {_format_sync_time(v)}")
    return "\n".join(lines)


def _format_sync_time(value: Any) -> str:
    """Render a unix timestamp as local-time ISO 8601.

    Keeps raw strings/ints untouched so forward-compat isn't broken.
    """
    from datetime import datetime, timezone
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return str(value)
    try:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
        return dt.strftime("%Y-%m-%d %H:%M:%S %Z")
    except (OverflowError, OSError, ValueError):
        return str(value)


def error(msg: str) -> None:
    Console(stderr=True).print(f"[{Color.ERROR}]error:[/] {msg}")


def warn(msg: str) -> None:
    Console(stderr=True).print(f"[{Color.WARN}]warning:[/] {msg}")


# ------------------------------------------------------------------ ls/tree


def format_ls(
    target: Path,
    entries: list[dict],
    preset: str,
    params: DensityParams,
    output_json: bool = False,
    cont_cap: int | None = None,
) -> str:
    if output_json:
        hits = [
            Hit(
                source=e["path"],
                lines=None,
                content=e.get("summary", "") or "",
                score=None,
                metadata={
                    "kind": "ls",
                    "name": e["name"],
                    "is_dir": bool(e["is_dir"]),
                    "indexed": bool(e.get("indexed", False)),
                    "summary_state": "stale" if e.get("stale") else "ok",
                },
            ).to_dict()
            for e in entries
        ]
        return _dumps(hits)
    if not entries:
        return f"{target}: (empty)"

    # How many continuation lines to render under each filename.
    # `cont_cap` lets the caller override the default per-preset value so an
    # explicit -H / --deep actually changes the on-screen output.
    if cont_cap is None:
        if preset == "peek":
            cont_cap = 0
        elif preset == "deep":
            cont_cap = 12
        else:
            cont_cap = 2

    lines = [f"{target}/"]
    name_col = max((len(e["name"]) + (1 if e["is_dir"] else 0)) for e in entries)
    name_col = min(name_col, 40)
    for e in entries:
        name = e["name"] + ("/" if e["is_dir"] else "")
        summary = e.get("summary", "") or ""
        if preset == "peek":
            lines.append(name)
            continue
        flags = []
        if e.get("indexed"):
            flags.append("indexed")
        if e.get("stale"):
            flags.append("summary:stale")
        suffix = f"  [{' '.join(flags)}]" if flags else ""
        if summary:
            head, _, tail = summary.partition("\n")
            lines.append(f"{name:<{name_col}}  {head}{suffix}")
            if tail and cont_cap > 0:
                indent = " " * (name_col + 2)
                for cont in tail.splitlines()[:cont_cap]:
                    lines.append(indent + cont)
        else:
            lines.append(f"{name}{suffix}")
    return "\n".join(lines)


def format_tree(
    target: Path,
    node: dict,
    preset: str,
    params: DensityParams,
    output_json: bool = False,
) -> str:
    if output_json:
        hits = _tree_as_hits(node, depth=0)
        return _dumps([h.to_dict() for h in hits])
    lines: list[str] = [f"{target}/"]
    _tree_render(node, lines, prefix="", is_last=True, preset=preset, params=params, is_root=True)
    return "\n".join(lines)


def _tree_as_hits(node: dict, depth: int) -> list[Hit]:
    """Flatten the tree into a list of ``Hit`` nodes for --json consumers.

    The root directory is emitted as depth=0. Each descendant records its
    depth in ``metadata.depth`` so callers can reconstruct the hierarchy
    without replaying the walk.
    """
    hits: list[Hit] = []
    hits.append(
        Hit(
            source=node.get("path", node.get("name", "")),
            lines=None,
            content=node.get("summary", "") or "",
            score=None,
            metadata={
                "kind": "tree",
                "name": node.get("name", ""),
                "is_dir": bool(node.get("is_dir", False)),
                "depth": depth,
                "summarizable": bool(node.get("summarizable", True)),
            },
        )
    )
    for child in node.get("children") or []:
        hits.extend(_tree_as_hits(child, depth + 1))
    return hits


def _tree_render(
    node: dict,
    lines: list[str],
    prefix: str,
    is_last: bool,
    preset: str,
    params: DensityParams,
    is_root: bool = False,
) -> None:
    children = node.get("children") or []
    if is_root:
        # Root already printed by caller
        pass
    else:
        branch = "└── " if is_last else "├── "
        name = node["name"] + ("/" if node["is_dir"] else "")
        summary = _node_summary(node, preset, params)
        if summary:
            lines.append(f"{prefix}{branch}{name}  — {summary}")
        else:
            lines.append(f"{prefix}{branch}{name}")
        prefix = prefix + ("    " if is_last else "│   ")

    for i, child in enumerate(children):
        _tree_render(
            child, lines, prefix, is_last=(i == len(children) - 1),
            preset=preset, params=params,
        )


def _node_summary(node: dict, preset: str, params: DensityParams) -> str:
    if preset == "peek":
        return ""
    if node.get("is_dir"):
        return node.get("summary", "").split("\n", 1)[0][:120]
    # Skip non-summarizable files (binaries, unknown types) — density extraction
    # on those produces garbage.
    if not node.get("summarizable", True):
        return ""
    path = Path(node["path"]) if node.get("path") else None
    if path is None or not path.exists():
        return ""
    try:
        view = density_view_for_path(path, preset, w_override=params.w,
                                     h_override=params.h, d_override=params.d)
    except Exception:
        return ""
    meaningful = [ln.strip() for ln in view.splitlines() if ln.strip()]
    if not meaningful:
        return ""
    # Tree renders one compact line per node. For deep we concatenate the
    # first few meaningful lines so the richer view is actually visible;
    # for skim / no-preset we stay with just the first line.
    if preset == "deep":
        summary = " · ".join(meaningful[:3])
        cap = max(240, params.w * 2 if params.w > 0 else 0)
    else:
        summary = meaningful[0]
        cap = max(120, params.w) if params.w > 0 else 120
    if len(summary) > cap:
        summary = summary[: cap - 1].rstrip() + "…"
    return summary


# -------------------------------------------------------------------- cat


def format_cat_density(
    content: str,
    content_type: str,
    params: DensityParams,
    *,
    show_line_numbers: bool = True,
    total_lines: int | None = None,
) -> str:
    """Render a density view with optional right-aligned source line numbers.

    Used by ``mfs cat --peek/--skim/--deep`` so Agents can locate sections
    without a follow-up grep. ``total_lines`` controls the number-column
    width (falls back to the max line number present in the extraction).
    """
    rows = extract_density_view_with_lines(content, content_type, params)
    if not show_line_numbers:
        return "\n".join(text for _ln, text in rows)

    numbers = [ln for ln, _t in rows if ln is not None]
    if total_lines is None:
        total_lines = max(numbers) if numbers else 1
    width = max(3, len(str(max(1, total_lines))))
    pad = " " * width

    rendered: list[str] = []
    for ln, text in rows:
        prefix = f"{ln:>{width}}" if ln is not None else pad
        rendered.append(f"{prefix}  {text}")
    return "\n".join(rendered)
