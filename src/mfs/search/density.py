"""W/H/D information density control for cat/ls/tree.

W (Width): chars per node (0 = title-only)
H (Height): max nodes from top
D (Depth): levels to expand (None for 2D file types)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .. import constants as C


@dataclass
class DensityParams:
    w: int
    h: int
    d: int | None


# --------------------------------------------------------------------- routing


def detect_density_type(extension: str, is_dir: bool = False) -> str:
    if is_dir:
        return "directory"
    ext = extension.lower()
    if ext in C.MARKDOWN_EXTENSIONS:
        return "markdown"
    if ext in C.CODE_EXTENSIONS:
        return "code"
    if ext in {".json"}:
        return "json"
    if ext in {".jsonl", ".ndjson"}:
        return "jsonl"
    if ext in {".csv", ".tsv"}:
        return "csv"
    return "text"


def resolve_density(
    content_type: str,
    preset: str | None,
    w_override: int | None = None,
    h_override: int | None = None,
    d_override: int | None = None,
) -> DensityParams:
    """Resolve final W/H/D values.

    Priority: explicit override > preset > type default (skim).
    """
    base = C.WHD_PRESETS.get(content_type, C.WHD_PRESETS["text"])
    preset_key = preset or "skim"
    w, h, d = base.get(preset_key, base["skim"])
    if w_override is not None:
        w = w_override
    if h_override is not None:
        h = h_override
    if d_override is not None:
        d = d_override
    return DensityParams(w=int(w), h=int(h), d=(None if d is None else int(d)))


def extract_density_view(content: str, content_type: str, params: DensityParams) -> str:
    if content_type == "markdown":
        return extract_markdown_density(content, params.w, params.h, params.d or 3)
    if content_type == "code":
        return extract_code_density(content, params.w, params.h, params.d or 2)
    if content_type == "json":
        return extract_json_density(content, params.w, params.h, params.d or 2)
    if content_type == "jsonl":
        return extract_jsonl_density(content, params.w, params.h)
    if content_type == "csv":
        return extract_csv_density(content, params.w, params.h)
    if content_type == "directory":
        # Caller provides the already-formatted directory summary.
        return content.strip()
    return extract_text_density(content, params.w, params.h)


# ---------------- Line-annotated variant -------------------------------
#
# cat --peek / --skim / --deep renders source line numbers alongside the
# extracted skeleton so Agents can jump straight to a heading / symbol with
# ``mfs cat -n L:L`` without a second grep pass. Each row is
# ``(line_no, text)`` where ``line_no`` is 1-based and ``None`` for purely
# synthesized lines (e.g. "... (N more headings)").


def extract_density_view_with_lines(
    content: str, content_type: str, params: DensityParams
) -> list[tuple[int | None, str]]:
    if content_type == "markdown":
        return extract_markdown_density_with_lines(
            content, params.w, params.h, params.d or 3
        )
    if content_type == "code":
        return extract_code_density_with_lines(
            content, params.w, params.h, params.d or 2
        )
    if content_type == "json":
        return _rehydrate_lines(
            extract_json_density(content, params.w, params.h, params.d or 2)
        )
    if content_type == "jsonl":
        return extract_jsonl_density_with_lines(content, params.w, params.h)
    if content_type == "csv":
        return extract_csv_density_with_lines(content, params.w, params.h)
    if content_type == "directory":
        return _rehydrate_lines(content.strip())
    return extract_text_density_with_lines(content, params.w, params.h)


def _rehydrate_lines(text: str) -> list[tuple[int | None, str]]:
    return [(None, ln) for ln in text.splitlines()]


def _char_offset_to_line(content: str, offset: int) -> int:
    """Translate a character offset to a 1-based line number."""
    if offset <= 0:
        return 1
    return content.count("\n", 0, offset) + 1


# ----------------------------------------------------------------- Markdown


_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)


def _mask_fenced_code(content: str) -> str:
    """Replace fenced code blocks with blanks of equal length to keep offsets."""
    def _repl(m: re.Match[str]) -> str:
        s = m.group(0)
        # Preserve newlines so line-based offsets remain stable
        return "".join(ch if ch == "\n" else " " for ch in s)
    return _CODE_FENCE_RE.sub(_repl, content)


def extract_markdown_density(content: str, w: int, h: int, d: int) -> str:
    return "\n".join(
        text for _ln, text in extract_markdown_density_with_lines(content, w, h, d)
    )


def extract_markdown_density_with_lines(
    content: str, w: int, h: int, d: int
) -> list[tuple[int | None, str]]:
    """Heading tree with per-line source line numbers.

    - `w=0` → heading-only skeleton
    - `w>0` → heading + first non-empty paragraph truncated to `w` chars
    - `h` caps the number of headings emitted
    - `d` caps the heading level displayed
    """
    masked = _mask_fenced_code(content)
    headings = [
        (len(m.group(1)), m.group(2).strip(), m.start(), m.end())
        for m in _MD_HEADING_RE.finditer(masked)
    ]
    headings = [h_ for h_ in headings if h_[0] <= max(1, d)]

    if not headings:
        return extract_text_density_with_lines(content, w=max(w, 60), h=h)

    rows: list[tuple[int | None, str]] = []
    for i, (level, text, s, e) in enumerate(headings[:h]):
        indent = "  " * (level - 1)
        heading_ln = _char_offset_to_line(content, s)
        rows.append((heading_ln, f"{indent}{'#' * level} {text}"))
        if w <= 0:
            continue
        next_start = headings[i + 1][2] if i + 1 < len(headings) else len(content)
        body = content[e:next_start]
        first_para, para_offset = _first_paragraph_with_offset(body)
        if first_para:
            para_ln = _char_offset_to_line(content, e + para_offset)
            rows.append((para_ln, f"{indent}  {_truncate(first_para, w)}"))
    if len(headings) > h:
        rows.append((None, f"... ({len(headings) - h} more headings)"))
    return rows


def _first_paragraph(body: str) -> str:
    text, _ = _first_paragraph_with_offset(body)
    return text


def _first_paragraph_with_offset(body: str) -> tuple[str, int]:
    """Return the first non-empty paragraph and its character offset in ``body``."""
    cursor = 0
    for block in body.split("\n\n"):
        s = block.strip()
        block_len = len(block) + 2  # include "\n\n" separator
        if not s or s.startswith("```") or _MD_HEADING_RE.match(s):
            cursor += block_len
            continue
        # Locate the first non-whitespace char inside the block.
        leading = len(block) - len(block.lstrip())
        absolute = cursor + leading
        s = re.sub(r"^[-*>]\s+", "", s, flags=re.MULTILINE)
        return " ".join(s.split()), absolute
    return "", 0


# --------------------------------------------------------------------- Code


_CODE_SYMBOL_RE = re.compile(
    r"^(?P<indent>[ \t]*)"
    r"(?P<kind>async\s+def|def|class|function|func|fn)\s+"
    r"(?P<name>[A-Za-z_][\w]*)\s*\(?",
    re.MULTILINE,
)


def extract_code_density(content: str, w: int, h: int, d: int) -> str:
    return "\n".join(
        text for _ln, text in extract_code_density_with_lines(content, w, h, d)
    )


def extract_code_density_with_lines(
    content: str, w: int, h: int, d: int
) -> list[tuple[int | None, str]]:
    symbols = [
        (len((m.group("indent") or "").expandtabs(4)), m.group("kind"), m.group("name"),
         m.start(), m.end())
        for m in _CODE_SYMBOL_RE.finditer(content)
    ]
    symbols = [s for s in symbols if (s[0] // 4) < max(1, d)]

    if not symbols:
        return extract_text_density_with_lines(content, w=max(w, 60), h=h)

    rows: list[tuple[int | None, str]] = []
    for i, (indent, kind, name, s_off, e) in enumerate(symbols[:h]):
        pad = " " * indent
        line_no = _char_offset_to_line(content, s_off)
        row_text = f"{pad}{kind} {name}"
        if w > 0:
            first_line_end = content.find("\n", e)
            signature = content[e:first_line_end if first_line_end != -1 else len(content)]
            signature = signature.strip()
            if signature:
                row_text = f"{pad}{kind} {name}{signature}"[: w + 40]
        rows.append((line_no, row_text))
    if len(symbols) > h:
        rows.append((None, f"... ({len(symbols) - h} more symbols)"))
    return rows


# --------------------------------------------------------------------- JSON


def extract_json_density(content: str, w: int, h: int, d: int) -> str:
    try:
        obj = json.loads(content)
    except json.JSONDecodeError:
        return extract_text_density(content, w=max(w, 60), h=h)
    lines: list[str] = []
    _render_json(obj, lines, indent=0, max_depth=max(1, d), w=w, h=h)
    if len(lines) > h:
        lines = lines[:h] + [f"... ({len(lines) - h} more keys)"]
    return "\n".join(lines)


def _render_json(obj, lines: list[str], indent: int, max_depth: int, w: int, h: int) -> None:
    pad = "  " * indent
    if isinstance(obj, dict):
        for k, v in obj.items():
            if len(lines) >= h + 1:
                break
            if isinstance(v, (dict, list)) and indent + 1 < max_depth:
                lines.append(f"{pad}{k}:")
                _render_json(v, lines, indent + 1, max_depth, w, h)
            else:
                val = _format_json_value(v, w)
                lines.append(f"{pad}{k}: {val}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if len(lines) >= h + 1:
                break
            if isinstance(v, (dict, list)) and indent + 1 < max_depth:
                lines.append(f"{pad}[{i}]:")
                _render_json(v, lines, indent + 1, max_depth, w, h)
            else:
                val = _format_json_value(v, w)
                lines.append(f"{pad}[{i}]: {val}")
    else:
        lines.append(f"{pad}{_format_json_value(obj, w)}")


def _format_json_value(v, w: int) -> str:
    if w <= 0:
        if isinstance(v, (dict, list)):
            return "{...}" if isinstance(v, dict) else "[...]"
        return ""
    if isinstance(v, (dict, list)):
        return _truncate(json.dumps(v, ensure_ascii=False), w)
    if isinstance(v, str):
        return _truncate(json.dumps(v, ensure_ascii=False), w)
    return str(v)


# ------------------------------------------------------------------- JSONL


def extract_jsonl_density(content: str, w: int, h: int) -> str:
    return "\n".join(
        text for _ln, text in extract_jsonl_density_with_lines(content, w, h)
    )


def extract_jsonl_density_with_lines(
    content: str, w: int, h: int
) -> list[tuple[int | None, str]]:
    raw_lines = content.splitlines()
    non_empty: list[tuple[int, str]] = [
        (i + 1, ln) for i, ln in enumerate(raw_lines) if ln.strip()
    ]
    rows: list[tuple[int | None, str]] = [
        (src_ln, _truncate(ln, w)) for src_ln, ln in non_empty[:h]
    ]
    if len(non_empty) > h:
        rows.append((None, f"... ({len(non_empty) - h} more lines)"))
    return rows


# --------------------------------------------------------------------- CSV


def extract_csv_density(content: str, w: int, h: int) -> str:
    return "\n".join(
        text for _ln, text in extract_csv_density_with_lines(content, w, h)
    )


def extract_csv_density_with_lines(
    content: str, w: int, h: int
) -> list[tuple[int | None, str]]:
    raw = content.splitlines()
    if not raw:
        return []
    out: list[tuple[int | None, str]] = []
    for idx, row in enumerate(raw[:h], start=1):
        cells = [_truncate(cell.strip(), w) for cell in row.split(",")]
        out.append((idx, " | ".join(cells)))
    if len(raw) > h:
        out.append((None, f"... ({len(raw) - h} more rows)"))
    return out


# --------------------------------------------------------------------- Text


def extract_text_density(content: str, w: int, h: int) -> str:
    return "\n".join(
        text for _ln, text in extract_text_density_with_lines(content, w, h)
    )


def extract_text_density_with_lines(
    content: str, w: int, h: int
) -> list[tuple[int | None, str]]:
    # Track paragraph start lines so the rendered summary can reference the
    # actual spot in the source file.
    paras: list[tuple[int, str]] = []
    cursor = 0
    for block in content.split("\n\n"):
        if block.strip():
            leading = len(block) - len(block.lstrip())
            start_char = cursor + leading
            paras.append((_char_offset_to_line(content, start_char), block.strip()))
        cursor += len(block) + 2
    if not paras:
        # Degenerate one-long-line content — fall back to raw line indexing.
        paras = [
            (i + 1, ln)
            for i, ln in enumerate(content.splitlines())
            if ln.strip()
        ]
    rows: list[tuple[int | None, str]] = [
        (ln, _truncate(" ".join(p.split()), w)) for ln, p in paras[:h]
    ]
    if len(paras) > h:
        rows.append((None, f"... ({len(paras) - h} more)"))
    return rows


# ---------------------------------------------------------------- helpers


def _truncate(s: str, w: int) -> str:
    if w <= 0 or len(s) <= w:
        return s
    return s[: max(0, w - 1)].rstrip() + "…"


def density_view_for_path(
    path: Path,
    preset: str | None,
    w_override: int | None = None,
    h_override: int | None = None,
    d_override: int | None = None,
) -> str:
    """Convenience: read `path`, detect type, render density view."""
    ext = path.suffix.lower()
    ctype = detect_density_type(ext)
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    params = resolve_density(ctype, preset, w_override, h_override, d_override)
    return extract_density_view(content, ctype, params)
