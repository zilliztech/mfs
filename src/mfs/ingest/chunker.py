"""Chunking engine: splits files into searchable chunks.

Routes by extension:
- Markdown (.md, .rst, .markdown): heading-based split with paragraph/character fallback
- Code (.py, .js, ...): top-level regex symbol split (AST is a future improvement)
- Text (.txt) and anything else: paragraph + character fallback

The output chunk's `chunk_text` is the raw content that will be embedded.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import yaml
from langchain_text_splitters import RecursiveCharacterTextSplitter

from .. import constants as C

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
# Matches top-level definitions across a handful of mainstream languages.
CODE_SYMBOL_RE = re.compile(
    r"^(?P<indent>[ \t]*)"
    r"(?P<kind>"
    r"(?:async\s+def|def|class)"                             # Python
    r"|function"                                             # JS/TS
    r"|(?:export\s+)?(?:async\s+)?(?:function|class)"        # JS/TS export
    r"|(?:public|private|protected|internal|static|final)\s+[\w<>\[\]]+\s+[\w]+\s*\("  # Java/C#-ish
    r"|func"                                                 # Go/Swift
    r"|fn"                                                   # Rust
    r")\b",
    re.MULTILINE,
)


@dataclass
class Chunk:
    chunk_index: int
    start_line: int
    end_line: int
    text: str
    content_type: str
    metadata: dict = field(default_factory=dict)


def generate_chunk_id(
    source: str,
    start_line: int,
    end_line: int,
    content_hash: str,
    embed_model: str,
) -> str:
    """Deterministic chunk ID: sha256("{source}:{start}:{end}:{content}:{model}")[:16]."""
    raw = f"{source}:{start_line}:{end_line}:{content_hash}:{embed_model}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def extract_frontmatter(content: str) -> tuple[dict | None, str, int]:
    """Return (frontmatter_dict, body, line_offset).

    line_offset = number of lines the frontmatter occupied (0 if none).
    """
    match = FRONTMATTER_RE.match(content)
    if not match:
        return None, content, 0
    try:
        fm = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None, content, 0
    if not isinstance(fm, dict):
        fm = None
    body = content[match.end():]
    line_offset = content[: match.end()].count("\n")
    return fm, body, line_offset


def content_type_for_extension(extension: str) -> str:
    if extension in C.MARKDOWN_EXTENSIONS:
        return C.CONTENT_TYPE_MARKDOWN
    if extension in C.CODE_EXTENSIONS:
        return C.CONTENT_TYPE_CODE
    return C.CONTENT_TYPE_TEXT


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def chunk_file(path: Path, content: str, extension: str) -> list[Chunk]:
    """Route to the appropriate chunker based on file extension."""
    if extension in C.MARKDOWN_EXTENSIONS:
        return chunk_markdown(content)
    if extension in C.CODE_EXTENSIONS:
        return chunk_code(content, extension)
    return chunk_text(content)


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def chunk_markdown(content: str) -> list[Chunk]:
    """Markdown chunking with 3-level fallback:

    1. Split by heading (#, ##, ###) — each section is one chunk
    2. If section > CHUNK_SIZE → split by paragraph (\\n\\n)
    3. If paragraph still > CHUNK_SIZE → RecursiveCharacterTextSplitter
    """
    frontmatter, body, fm_line_offset = extract_frontmatter(content)
    sections = _split_markdown_by_heading(body)

    # Prepend frontmatter to the first non-empty section's text as a pseudo header
    if frontmatter and sections:
        fm_yaml = yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip()
        sections[0] = (
            sections[0][0],
            sections[0][1],
            sections[0][2],
            f"---\n{fm_yaml}\n---\n{sections[0][3]}",
        )

    chunks: list[Chunk] = []
    for heading_level, heading_text, start_line_in_body, section_text in sections:
        # Translate line numbers: add fm_line_offset to get absolute 1-based lines
        abs_start = start_line_in_body + fm_line_offset
        section_lines = section_text.count("\n")
        abs_end = abs_start + section_lines

        if len(section_text) <= C.CHUNK_SIZE:
            chunks.append(
                Chunk(
                    chunk_index=len(chunks),
                    start_line=abs_start,
                    end_line=max(abs_start, abs_end),
                    text=section_text.rstrip("\n"),
                    content_type=C.CONTENT_TYPE_MARKDOWN,
                    metadata={
                        "heading_level": heading_level,
                        "heading_text": heading_text,
                        "has_code_block": "```" in section_text,
                    },
                )
            )
            continue

        # Section too long → paragraph split
        sub_chunks = _split_long_block(section_text, abs_start)
        for sc in sub_chunks:
            sc.chunk_index = len(chunks)
            sc.content_type = C.CONTENT_TYPE_MARKDOWN
            sc.metadata.update(
                {
                    "heading_level": heading_level,
                    "heading_text": heading_text,
                    "has_code_block": "```" in sc.text,
                }
            )
            chunks.append(sc)

    if not chunks and body.strip():
        # File with no headings → fall back to text chunking, but tag as markdown
        for tc in chunk_text(body):
            tc.content_type = C.CONTENT_TYPE_MARKDOWN
            chunks.append(tc)

    return chunks


def _split_markdown_by_heading(content: str) -> list[tuple[int, str, int, str]]:
    """Return list of (heading_level, heading_text, start_line_1based, section_text).

    A "section" is the block from one heading up to (but not including) the next.
    If content has no heading the entire body is one section with level=0.
    """
    if not content.strip():
        return []

    matches = list(HEADING_RE.finditer(content))
    if not matches:
        return [(0, "", 1, content)]

    sections: list[tuple[int, str, int, str]] = []

    # Preamble before first heading
    first = matches[0]
    if first.start() > 0:
        preamble = content[: first.start()]
        if preamble.strip():
            sections.append((0, "", 1, preamble))

    for i, m in enumerate(matches):
        level = len(m.group(1))
        text = m.group(2).strip()
        start_line = content.count("\n", 0, m.start()) + 1
        end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        section_text = content[m.start():end_pos]
        sections.append((level, text, start_line, section_text))

    return sections


def _split_long_block(text: str, abs_start_line: int) -> list[Chunk]:
    """Split an oversized section into sub-chunks using paragraph + char fallback."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=C.CHUNK_SIZE,
        chunk_overlap=C.CHUNK_OVERLAP,
        separators=["\n\n", "\n", " ", ""],
    )
    pieces = splitter.split_text(text)
    chunks: list[Chunk] = []
    cursor = 0  # char offset into text
    for piece in pieces:
        if not piece:
            continue
        idx = text.find(piece, cursor)
        if idx == -1:
            idx = cursor
        start_line = abs_start_line + text.count("\n", 0, idx)
        end_line = start_line + piece.count("\n")
        chunks.append(
            Chunk(
                chunk_index=0,
                start_line=start_line,
                end_line=max(start_line, end_line),
                text=piece,
                content_type=C.CONTENT_TYPE_TEXT,
                metadata={},
            )
        )
        cursor = idx + max(1, len(piece) - C.CHUNK_OVERLAP)
    return chunks


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------


def chunk_text(content: str) -> list[Chunk]:
    """Plain-text chunking: paragraph split + character fallback."""
    if not content.strip():
        return []

    if len(content) <= C.CHUNK_SIZE:
        return [
            Chunk(
                chunk_index=0,
                start_line=1,
                end_line=max(1, content.count("\n") + 1),
                text=content.rstrip("\n"),
                content_type=C.CONTENT_TYPE_TEXT,
                metadata={},
            )
        ]

    pieces = _split_long_block(content, abs_start_line=1)
    for i, p in enumerate(pieces):
        p.chunk_index = i
        p.content_type = C.CONTENT_TYPE_TEXT
    return pieces


# ---------------------------------------------------------------------------
# Code (simple regex-based for MVP)
# ---------------------------------------------------------------------------


def chunk_code(content: str, extension: str) -> list[Chunk]:
    """Tree-sitter AST chunking with a regex fallback.

    We first try :func:`ast_chunker.chunk_code_ast` — real AST parsing produces
    one chunk per top-level definition with accurate ``symbol_name`` /
    ``symbol_type`` metadata. If that returns ``None`` (unsupported language,
    missing parser, or a totally broken parse), we fall back to the legacy
    regex chunker below so we still produce *something* searchable.
    """
    if not content.strip():
        return []

    # --- AST path -----------------------------------------------------------
    try:
        from .ast_chunker import chunk_code_ast
        ast_result = chunk_code_ast(content, extension)
    except Exception:
        ast_result = None

    if ast_result is not None:
        language = _language_for_ext(extension)
        chunks: list[Chunk] = []
        for i, ac in enumerate(ast_result):
            meta: dict = {
                "language": language,
                "symbol_type": ac.symbol_type,
            }
            if ac.symbol_name:
                meta["symbol_name"] = ac.symbol_name
            chunks.append(
                Chunk(
                    chunk_index=i,
                    start_line=ac.start_line,
                    end_line=ac.end_line,
                    text=ac.text,
                    content_type=C.CONTENT_TYPE_CODE,
                    metadata=meta,
                )
            )
        return chunks

    # --- Regex fallback -----------------------------------------------------
    # Consider only markers with zero or near-zero indentation as "top-level" enough.
    markers = [
        (m.start(), m.end(), m.group("indent") or "", m.group("kind"))
        for m in CODE_SYMBOL_RE.finditer(content)
    ]
    top = [m for m in markers if len(m[2]) <= 4]

    if not top:
        # No clear symbols → treat as text
        chunks = chunk_text(content)
        for c in chunks:
            c.content_type = C.CONTENT_TYPE_CODE
            c.metadata["language"] = _language_for_ext(extension)
        return chunks

    chunks: list[Chunk] = []
    # Preamble
    first_start = top[0][0]
    if first_start > 0:
        preamble = content[:first_start]
        if preamble.strip():
            chunks.extend(_emit_code_segment(preamble, 1, None, extension))

    for i, (start, _end, _indent, kind) in enumerate(top):
        next_start = top[i + 1][0] if i + 1 < len(top) else len(content)
        segment = content[start:next_start]
        start_line = content.count("\n", 0, start) + 1
        symbol_name = _extract_symbol_name(segment, kind)
        chunks.extend(
            _emit_code_segment(
                segment,
                start_line,
                {"kind": kind.strip(), "symbol_name": symbol_name},
                extension,
            )
        )

    for i, c in enumerate(chunks):
        c.chunk_index = i
    return chunks


def _emit_code_segment(
    segment: str,
    start_line: int,
    symbol_meta: dict | None,
    extension: str,
) -> list[Chunk]:
    meta: dict = {"language": _language_for_ext(extension)}
    if symbol_meta:
        meta.update(symbol_meta)

    if len(segment) <= C.CHUNK_SIZE:
        return [
            Chunk(
                chunk_index=0,
                start_line=start_line,
                end_line=start_line + segment.count("\n"),
                text=segment.rstrip("\n"),
                content_type=C.CONTENT_TYPE_CODE,
                metadata=meta,
            )
        ]

    # Fall back to char split
    pieces = _split_long_block(segment, start_line)
    for p in pieces:
        p.content_type = C.CONTENT_TYPE_CODE
        p.metadata = dict(meta)
    return pieces


def _extract_symbol_name(segment: str, kind: str) -> str:
    first_line = segment.splitlines()[0] if segment else ""
    # Common patterns: `def foo(`, `class Foo(`, `function foo(`, `func foo(`, `fn foo(`
    m = re.search(r"(?:def|class|function|func|fn)\s+([A-Za-z_][\w]*)", first_line)
    if m:
        return m.group(1)
    return first_line.strip()[:80]


_LANGUAGE_BY_EXT: dict[str, str] = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".tsx": "tsx", ".jsx": "javascript", ".go": "go", ".rs": "rust",
    ".java": "java", ".rb": "ruby", ".php": "php",
    ".c": "c", ".cpp": "cpp", ".h": "c", ".hpp": "cpp",
    ".cs": "csharp", ".scala": "scala", ".kt": "kotlin", ".swift": "swift",
    ".sh": "shell", ".bash": "shell", ".zsh": "shell",
    ".sql": "sql", ".proto": "proto", ".graphql": "graphql",
    ".tf": "terraform", ".hcl": "hcl",
}


def _language_for_ext(extension: str) -> str:
    return _LANGUAGE_BY_EXT.get(extension, extension.lstrip("."))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def chunks_for_path(path: Path) -> list[Chunk]:
    """Convenience helper: read `path` and chunk it based on extension."""
    ext = path.suffix.lower()
    content = path.read_text(encoding="utf-8", errors="replace")
    return chunk_file(path, content, ext)


def iter_chunks(chunks: Iterable[Chunk]) -> Iterable[Chunk]:
    for c in chunks:
        yield c
