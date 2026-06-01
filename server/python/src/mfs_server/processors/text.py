"""Body chunkers for document / code object_kinds.

Chonkie RecursiveChunker (markdown/text) + CodeChunker (AST/tree-sitter). Returns
(content, lines=[start,end]) per chunk; chonkie gives char offsets which we convert
to 1-based line numbers for the Milvus `lines` field / chunk_id.
"""

from __future__ import annotations

from functools import lru_cache

from chonkie import CodeChunker, RecursiveChunker

# ext -> tree-sitter language for CodeChunker
_LANG = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cc": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".sh": "bash",
    ".bash": "bash",
}


@lru_cache(maxsize=16)
def _recursive(chunk_size: int) -> RecursiveChunker:
    return RecursiveChunker(chunk_size=chunk_size)


@lru_cache(maxsize=32)
def _code(language: str, chunk_size: int) -> CodeChunker:
    return CodeChunker(language=language, chunk_size=chunk_size)


def _offset_to_line(text: str, off: int) -> int:
    return text.count("\n", 0, off) + 1


def chunk_body(
    content: str, object_kind: str, ext: str, chunk_size: int
) -> list[tuple[str, list[int]]]:
    if not content.strip():
        return []
    if object_kind == "code":
        lang = _LANG.get(ext.lower(), "auto")
        try:
            chunker = _code(lang, chunk_size)
        except Exception:
            chunker = _recursive(chunk_size)
    else:
        chunker = _recursive(chunk_size)
    try:
        chunks = chunker(content)
    except Exception:
        # fallback: recursive split if code parsing blows up (minified/syntax error)
        chunks = _recursive(chunk_size)(content)
    out: list[tuple[str, list[int]]] = []
    for c in chunks:
        if not c.text.strip():
            continue
        start_line = _offset_to_line(content, c.start_index)
        end_line = _offset_to_line(content, c.end_index)
        out.append((c.text, [start_line, end_line]))
    return out
