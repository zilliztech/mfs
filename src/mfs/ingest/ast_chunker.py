"""Tree-sitter AST-based code chunking.

Produces one chunk per top-level definition (function, class, method). Code
before the first definition (imports, module docstring, shebangs) becomes a
single "preamble" chunk.

Caller is expected to fall back to the regex chunker if :func:`chunk_code_ast`
returns ``None`` (parser unavailable or the entire parse failed).

Corner cases handled here:

- Over-long AST chunks (> ``CHUNK_SIZE * 3``) are re-split with
  :class:`RecursiveCharacterTextSplitter`; the split inherits the parent's
  ``symbol_name``/``symbol_type``.
- Runs of very short chunks (< 50 chars) are merged with their neighbor so
  we don't flood the index with one-line definitions.
- Empty / whitespace-only inputs return an empty list.
- Syntax errors don't raise — the parser still produces a best-effort tree
  and we return whatever definitions we can extract. If nothing sensible
  comes out, we return ``None`` so the caller falls back.
"""

from __future__ import annotations

from dataclasses import dataclass

from langchain_text_splitters import RecursiveCharacterTextSplitter

from .. import constants as C


# ------------------------------------------------------------------ language map

_EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".php": "php",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cc": "cpp",
    ".cs": "csharp",
    ".scala": "scala",
    ".kt": "kotlin",
    ".swift": "swift",
}

# Tree-sitter node types that represent a top-level definition we want to chunk.
# Kept as (language -> {node_type: symbol_kind}). The symbol_kind is what we
# expose in chunk.metadata.symbol_type.
_DEF_NODE_TYPES: dict[str, dict[str, str]] = {
    "python": {
        "function_definition": "function",
        "class_definition": "class",
        "decorated_definition": "function",  # decorator-wrapped def/class
    },
    "javascript": {
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
        "generator_function_declaration": "function",
        "lexical_declaration": "function",   # `const foo = () => {}`
        "variable_declaration": "function",  # `var foo = function() {}`
    },
    "typescript": {
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
        "interface_declaration": "class",
        "type_alias_declaration": "class",
        "lexical_declaration": "function",
    },
    "tsx": {
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
        "interface_declaration": "class",
        "type_alias_declaration": "class",
        "lexical_declaration": "function",
    },
    "go": {
        "function_declaration": "function",
        "method_declaration": "method",
        "type_declaration": "class",
    },
    "rust": {
        "function_item": "function",
        "impl_item": "class",
        "struct_item": "class",
        "enum_item": "class",
        "trait_item": "class",
        "mod_item": "class",
    },
    "java": {
        "method_declaration": "method",
        "class_declaration": "class",
        "interface_declaration": "class",
        "enum_declaration": "class",
    },
    "ruby": {
        "method": "method",
        "class": "class",
        "module": "class",
        "singleton_method": "method",
    },
    "php": {
        "function_definition": "function",
        "class_declaration": "class",
        "method_declaration": "method",
        "interface_declaration": "class",
        "trait_declaration": "class",
    },
    "c": {
        "function_definition": "function",
        "struct_specifier": "class",
        "enum_specifier": "class",
    },
    "cpp": {
        "function_definition": "function",
        "class_specifier": "class",
        "struct_specifier": "class",
        "namespace_definition": "class",
        "template_declaration": "function",
    },
    "csharp": {
        "method_declaration": "method",
        "class_declaration": "class",
        "interface_declaration": "class",
        "struct_declaration": "class",
        "enum_declaration": "class",
    },
    "scala": {
        "function_definition": "function",
        "class_definition": "class",
        "object_definition": "class",
        "trait_definition": "class",
    },
    "kotlin": {
        "function_declaration": "function",
        "class_declaration": "class",
        "object_declaration": "class",
    },
    "swift": {
        "function_declaration": "function",
        "class_declaration": "class",
        "protocol_declaration": "class",
        "enum_declaration": "class",
        "struct_declaration": "class",
    },
}


@dataclass
class AstChunk:
    """Intermediate representation — converted by the caller to :class:`Chunk`."""

    start_line: int  # 1-based inclusive
    end_line: int    # 1-based inclusive
    text: str
    symbol_type: str  # "function" | "class" | "method" | "preamble"
    symbol_name: str | None


MIN_CHUNK_CHARS = 50
OVERSIZE_MULTIPLIER = 3  # chunks larger than CHUNK_SIZE * this get re-split


# ------------------------------------------------------------------ public API


def language_for_extension(extension: str) -> str | None:
    return _EXT_TO_LANG.get(extension)


def chunk_code_ast(content: str, extension: str) -> list[AstChunk] | None:
    """AST-chunk ``content`` using tree-sitter.

    Returns ``None`` if the language isn't supported, tree-sitter isn't
    available, or parsing produced no usable output. Callers should fall back
    to the regex-based chunker on ``None``.
    """
    if not content.strip():
        return []

    language = _EXT_TO_LANG.get(extension)
    if language is None:
        return None

    node_types = _DEF_NODE_TYPES.get(language)
    if not node_types:
        return None

    try:
        from tree_sitter_language_pack import get_parser
    except ImportError:
        return None

    try:
        parser = get_parser(language)
    except LookupError:
        return None

    source_bytes = content.encode("utf-8", errors="replace")
    try:
        tree = parser.parse(source_bytes)
    except Exception:
        return None

    root = tree.root_node
    definitions = _collect_top_level_definitions(root, node_types)

    if not definitions and not root.has_error:
        # Empty file or script-style (no functions/classes): treat entire body
        # as one "preamble" chunk.
        return [
            AstChunk(
                start_line=1,
                end_line=max(1, content.count("\n") + 1),
                text=content.rstrip("\n"),
                symbol_type="preamble",
                symbol_name=None,
            )
        ]

    if not definitions:
        # Heavily broken file → let the caller fall back.
        return None

    chunks: list[AstChunk] = []

    # Preamble = everything before the first definition (imports, docstring,
    # shebang, module-level constants, ...).
    first_byte = definitions[0].start_byte
    if first_byte > 0:
        preamble_bytes = source_bytes[:first_byte]
        preamble_text = preamble_bytes.decode("utf-8", errors="replace")
        if preamble_text.strip():
            chunks.append(
                AstChunk(
                    start_line=1,
                    end_line=preamble_text.count("\n") + 1,
                    text=preamble_text.rstrip("\n"),
                    symbol_type="preamble",
                    symbol_name=None,
                )
            )

    # One chunk per top-level definition, inclusive of the run of whitespace
    # up to the next definition so line numbers line up cleanly.
    for i, node in enumerate(definitions):
        end_byte = (
            definitions[i + 1].start_byte if i + 1 < len(definitions)
            else len(source_bytes)
        )
        text = source_bytes[node.start_byte:end_byte].decode("utf-8", errors="replace")
        start_line = node.start_point[0] + 1
        end_line = start_line + text.count("\n")
        symbol_type = node_types.get(node.type, "function")
        symbol_name = _extract_symbol_name(node, source_bytes, language)
        chunks.append(
            AstChunk(
                start_line=start_line,
                end_line=end_line,
                text=text.rstrip("\n"),
                symbol_type=symbol_type,
                symbol_name=symbol_name,
            )
        )

    # Corner cases: split oversized chunks, merge tiny ones.
    chunks = _split_oversized(chunks)
    chunks = _merge_tiny(chunks)
    return chunks


# ------------------------------------------------------------------ internals


def _collect_top_level_definitions(root, node_types: dict[str, str]):
    """Return tree-sitter nodes directly under the module/program root."""
    # Most languages represent the root as module/program/source_file with
    # top-level siblings as children. We walk children only, never recursing.
    out = []
    for child in root.children:
        if child.type in node_types:
            out.append(child)
    return out


def _extract_symbol_name(node, source_bytes: bytes, language: str) -> str | None:
    """Best-effort: pull the identifier child of a definition node."""
    # Most tree-sitter grammars expose the symbol via a child field named "name"
    # or as a direct identifier child. We try `child_by_field_name("name")` first.
    try:
        name_node = node.child_by_field_name("name")
    except Exception:
        name_node = None
    if name_node is not None:
        return source_bytes[name_node.start_byte:name_node.end_byte].decode(
            "utf-8", errors="replace"
        )

    # Python's decorated_definition wraps the real def/class — recurse.
    if node.type == "decorated_definition":
        for child in node.children:
            if child.type in ("function_definition", "class_definition"):
                return _extract_symbol_name(child, source_bytes, language)

    # JS const-arrow: `const foo = () => {}` — the identifier lives in the
    # first variable_declarator.
    if node.type in ("lexical_declaration", "variable_declaration"):
        for child in node.children:
            if child.type == "variable_declarator":
                try:
                    n = child.child_by_field_name("name")
                except Exception:
                    n = None
                if n is not None:
                    return source_bytes[n.start_byte:n.end_byte].decode(
                        "utf-8", errors="replace"
                    )

    # Fall back: first identifier child.
    for child in node.children:
        if child.type in ("identifier", "type_identifier", "name"):
            return source_bytes[child.start_byte:child.end_byte].decode(
                "utf-8", errors="replace"
            )
    return None


def _split_oversized(chunks: list[AstChunk]) -> list[AstChunk]:
    """Re-split any chunk larger than CHUNK_SIZE * OVERSIZE_MULTIPLIER."""
    threshold = C.CHUNK_SIZE * OVERSIZE_MULTIPLIER
    out: list[AstChunk] = []
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=C.CHUNK_SIZE,
        chunk_overlap=C.CHUNK_OVERLAP,
        separators=["\n\n", "\n", " ", ""],
    )
    for c in chunks:
        if len(c.text) <= threshold:
            out.append(c)
            continue
        pieces = splitter.split_text(c.text)
        if not pieces:
            out.append(c)
            continue
        cursor = 0
        for piece in pieces:
            if not piece:
                continue
            idx = c.text.find(piece, cursor)
            if idx == -1:
                idx = cursor
            lines_before = c.text.count("\n", 0, idx)
            start_line = c.start_line + lines_before
            end_line = start_line + piece.count("\n")
            out.append(
                AstChunk(
                    start_line=start_line,
                    end_line=max(start_line, end_line),
                    text=piece,
                    symbol_type=c.symbol_type,
                    symbol_name=c.symbol_name,
                )
            )
            cursor = idx + max(1, len(piece) - C.CHUNK_OVERLAP)
    return out


def _merge_tiny(chunks: list[AstChunk]) -> list[AstChunk]:
    """Greedily merge consecutive chunks smaller than MIN_CHUNK_CHARS.

    We only merge within the same symbol_type to avoid gluing a preamble onto a
    function; a ``function``+``method`` or ``class``+``function`` pair stays
    separate even if short.
    """
    if not chunks:
        return chunks
    out: list[AstChunk] = []
    buf: AstChunk | None = None
    for c in chunks:
        if len(c.text) >= MIN_CHUNK_CHARS:
            if buf is not None:
                out.append(buf)
                buf = None
            out.append(c)
            continue
        if buf is None:
            buf = AstChunk(
                start_line=c.start_line,
                end_line=c.end_line,
                text=c.text,
                symbol_type=c.symbol_type,
                symbol_name=c.symbol_name,
            )
            continue
        # Merge only if same symbol_type; otherwise flush and restart.
        if buf.symbol_type != c.symbol_type:
            out.append(buf)
            buf = AstChunk(
                start_line=c.start_line,
                end_line=c.end_line,
                text=c.text,
                symbol_type=c.symbol_type,
                symbol_name=c.symbol_name,
            )
            continue
        buf.text = buf.text + "\n" + c.text
        buf.end_line = c.end_line
        # Keep the first symbol_name; the name of a cluster of tiny definitions
        # is inherently ambiguous, but the earliest one is most informative.
    if buf is not None:
        out.append(buf)
    return out


__all__ = [
    "AstChunk",
    "chunk_code_ast",
    "language_for_extension",
]
