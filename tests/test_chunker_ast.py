"""Tests for the tree-sitter AST code chunker and its fallbacks."""

from __future__ import annotations

from mfs.ingest.ast_chunker import (
    OVERSIZE_MULTIPLIER,
    chunk_code_ast,
)
from mfs.ingest.chunker import chunk_code
from mfs import constants as C


# ----------------------------------------------------------- python happy path


def test_python_basic_functions_and_classes():
    src = (
        '"""Module docstring."""\n'
        "import os\n"
        "\n"
        "CONST = 1\n"
        "\n"
        "def foo(x):\n"
        "    return x + 1\n"
        "\n"
        "class Bar:\n"
        "    def m(self):\n"
        "        return 42\n"
        "\n"
        "def baz():\n"
        "    pass\n"
    )
    chunks = chunk_code_ast(src, ".py")
    assert chunks is not None
    # preamble + foo + Bar + baz
    assert len(chunks) == 4
    assert chunks[0].symbol_type == "preamble"
    assert chunks[0].symbol_name is None
    assert "Module docstring" in chunks[0].text

    assert chunks[1].symbol_type == "function"
    assert chunks[1].symbol_name == "foo"
    assert chunks[1].text.lstrip().startswith("def foo")

    assert chunks[2].symbol_type == "class"
    assert chunks[2].symbol_name == "Bar"
    # the class chunk should contain its method
    assert "def m" in chunks[2].text

    assert chunks[3].symbol_type == "function"
    assert chunks[3].symbol_name == "baz"


def test_python_decorated_function_names_resolved():
    src = (
        "import functools\n"
        "\n"
        "@functools.lru_cache\n"
        "def cached(x):\n"
        "    return x\n"
    )
    chunks = chunk_code_ast(src, ".py")
    assert chunks is not None
    # preamble (import) + decorated cached
    cached = [c for c in chunks if c.symbol_name == "cached"]
    assert len(cached) == 1
    assert cached[0].symbol_type == "function"
    assert "@functools.lru_cache" in cached[0].text


def test_python_script_style_no_defs_emits_single_preamble():
    src = "print('hello')\nfor i in range(3):\n    print(i)\n"
    chunks = chunk_code_ast(src, ".py")
    assert chunks is not None
    assert len(chunks) == 1
    assert chunks[0].symbol_type == "preamble"
    assert "print('hello')" in chunks[0].text


def test_python_pure_comments_emits_preamble_only():
    src = "# just a comment\n# another comment\n"
    chunks = chunk_code_ast(src, ".py")
    assert chunks is not None
    assert len(chunks) == 1
    assert chunks[0].symbol_type == "preamble"
    assert "comment" in chunks[0].text


def test_empty_content_returns_empty_list():
    assert chunk_code_ast("", ".py") == []
    assert chunk_code_ast("   \n\n", ".py") == []


# ------------------------------------------------------------------- fallbacks


def test_unsupported_extension_returns_none():
    # .xyz isn't in the language map — AST path should bail out.
    assert chunk_code_ast("content\n", ".xyz") is None


def test_syntax_error_python_either_parses_best_effort_or_falls_back():
    """A malformed Python file shouldn't crash the chunker.

    Tree-sitter is permissive: it usually still returns nodes for whatever
    parts it could understand. We only require no exception and that the
    public entry point produces *some* chunks via either the AST or regex
    path.
    """
    src = "def foo(:\n    pass\n\ndef bar():\n    return 1\n"
    # Direct AST call may return chunks (best-effort) or None.
    direct = chunk_code_ast(src, ".py")
    # Top-level chunker always succeeds (falls back if AST bails).
    chunks = chunk_code(src, ".py")
    assert chunks, "chunk_code must always produce at least one chunk for non-empty input"
    assert any("bar" in c.text for c in chunks)
    # If AST did parse it, the resulting AstChunks should still be well-formed.
    if direct is not None:
        for c in direct:
            assert c.start_line >= 1
            assert c.end_line >= c.start_line


def test_oversized_function_splits_and_preserves_symbol_name():
    body = "    x = 1\n" * (C.CHUNK_SIZE * OVERSIZE_MULTIPLIER // 10 + 200)
    src = f"def huge():\n{body}\n"
    chunks = chunk_code_ast(src, ".py")
    assert chunks is not None
    assert len(chunks) >= 2, "oversized chunk should split into multiple pieces"
    # Every split piece should retain the parent symbol_name.
    huge_chunks = [c for c in chunks if c.symbol_name == "huge"]
    assert len(huge_chunks) >= 2
    # Lines should be monotonically non-decreasing across splits.
    lines = [(c.start_line, c.end_line) for c in huge_chunks]
    for i in range(1, len(lines)):
        assert lines[i][0] >= lines[i - 1][0]


def test_tiny_consecutive_functions_merge():
    # Three tiny one-liner functions — should be merged into one AstChunk since
    # they all share symbol_type "function".
    src = (
        "def a(): return 1\n"
        "def b(): return 2\n"
        "def c(): return 3\n"
    )
    chunks = chunk_code_ast(src, ".py")
    assert chunks is not None
    # Each def is < MIN_CHUNK_CHARS on its own, so the merger should
    # collapse them.
    assert len(chunks) == 1
    assert chunks[0].symbol_type == "function"
    assert "def a" in chunks[0].text and "def c" in chunks[0].text


def test_tiny_merge_respects_symbol_type_boundary():
    # A short class followed by a short function should NOT merge into one
    # chunk (different symbol_type).
    src = (
        "class A: pass\n"
        "def f(): pass\n"
    )
    chunks = chunk_code_ast(src, ".py")
    assert chunks is not None
    types = sorted(c.symbol_type for c in chunks)
    assert types == ["class", "function"]


# ------------------------------------------------------------ other languages


def test_javascript_function_and_class():
    src = (
        "function add(a, b) {\n  return a + b;\n}\n"
        "class Car {\n  drive() { return 'vroom'; }\n}\n"
    )
    chunks = chunk_code_ast(src, ".js")
    assert chunks is not None
    names = {c.symbol_name for c in chunks if c.symbol_name}
    assert {"add", "Car"} <= names


def test_javascript_const_arrow_function_captured():
    src = "const greet = (name) => `hello ${name}`;\n"
    chunks = chunk_code_ast(src, ".js")
    assert chunks is not None
    assert any(c.symbol_name == "greet" for c in chunks)


def test_ruby_language_supported_via_language_pack():
    src = (
        "class Greeter\n"
        "  def greet(name)\n"
        "    \"Hello, #{name}\"\n"
        "  end\n"
        "end\n"
    )
    chunks = chunk_code_ast(src, ".rb")
    assert chunks is not None
    assert any(c.symbol_name == "Greeter" for c in chunks)


def test_go_function_and_type_declarations():
    src = (
        "package main\n\n"
        "type Point struct { X, Y int }\n\n"
        "func main() { println(\"hi\") }\n"
    )
    chunks = chunk_code_ast(src, ".go")
    assert chunks is not None
    names = {c.symbol_name for c in chunks if c.symbol_name}
    assert "main" in names


# ------------------------------------------------------ integration via chunk_code


def test_chunk_code_populates_symbol_metadata():
    src = (
        "import os\n\n"
        "def alpha():\n    return 1\n\n"
        "class Beta:\n    def m(self): return 2\n"
    )
    chunks = chunk_code(src, ".py")
    assert len(chunks) >= 2
    by_name = {c.metadata.get("symbol_name"): c for c in chunks}
    assert "alpha" in by_name
    assert by_name["alpha"].metadata["symbol_type"] == "function"
    assert by_name["alpha"].metadata["language"] == "python"
    assert "Beta" in by_name
    assert by_name["Beta"].metadata["symbol_type"] == "class"


def test_chunk_code_fallback_for_unsupported_extension_still_produces_chunks():
    # `.clj` isn't in the AST language map but IS in CODE_EXTENSIONS — the
    # regex fallback must take over.
    src = "(defn add [a b] (+ a b))\n(defn mul [a b] (* a b))\n"
    chunks = chunk_code(src, ".clj")
    assert chunks
    assert chunks[0].content_type == C.CONTENT_TYPE_CODE


def test_chunk_code_empty_returns_empty_list():
    assert chunk_code("", ".py") == []
