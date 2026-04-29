"""Constants: file type classifications, chunk parameters, W/H/D presets."""

from __future__ import annotations

# ---- File type classification ----

INDEXED_EXTENSIONS: set[str] = {
    # Markdown
    ".md", ".rst", ".markdown",
    # Code (tree-sitter supported, but we use regex chunking for MVP)
    ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".kt",
    ".swift", ".rb", ".php", ".c", ".cpp", ".h", ".hpp", ".cs", ".scala", ".clj",
    # Script / Schema / IaC
    ".sh", ".bash", ".zsh", ".sql", ".proto", ".graphql", ".tf", ".hcl",
    # Text
    ".txt",
    # Conversion targets. PDF and DOCX are converted to Markdown before chunking.
    # Keep other binary document formats out of the indexed set until we have
    # a converter for them; otherwise they would be read as raw bytes and
    # produce binary-noise chunks.
    ".pdf", ".docx",
}

# Extensions that go through a text converter before chunking. The converter
# output is Markdown so we route into the Markdown chunker path.
CONVERTED_EXTENSIONS: set[str] = {".pdf", ".docx"}

NOT_INDEXED_EXTENSIONS: set[str] = {
    ".json", ".yaml", ".yml", ".toml", ".ini", ".env",
    ".csv", ".tsv", ".jsonl", ".ndjson",
    ".html", ".htm", ".xml", ".css", ".scss", ".sass", ".less",
    ".log",
}

IGNORED_EXTENSIONS: set[str] = {
    ".pyc", ".pyo", ".class", ".o", ".so", ".dll", ".dylib", ".exe", ".bin",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
    ".mp3", ".mp4", ".wav", ".mov",
    ".zip", ".tar", ".gz", ".7z",
    ".lock",
}

IGNORED_FILENAMES: set[str] = {
    "package-lock.json",
}

# Directories excluded from scanning
IGNORED_DIRNAMES: set[str] = {
    ".git", ".hg", ".svn",
    "node_modules", ".venv", "venv", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "dist", "build", ".idea", ".vscode",
}

# Content type labels stored in Milvus
CONTENT_TYPE_MARKDOWN = "markdown"
CONTENT_TYPE_CODE = "code"
CONTENT_TYPE_TEXT = "text"
CONTENT_TYPE_DIRECTORY = "directory"
CONTENT_TYPE_LLM_SUMMARY = "llm_summary"
CONTENT_TYPE_VLM_DESCRIPTION = "vlm_description"

# Mapping extension -> content type
MARKDOWN_EXTENSIONS: set[str] = {".md", ".rst", ".markdown"}
CODE_EXTENSIONS: set[str] = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".kt",
    ".swift", ".rb", ".php", ".c", ".cpp", ".h", ".hpp", ".cs", ".scala", ".clj",
    ".sh", ".bash", ".zsh", ".sql", ".proto", ".graphql", ".tf", ".hcl",
}
TEXT_EXTENSIONS: set[str] = {".txt"}

# ---- Chunk parameters ----

CHUNK_SIZE = 1500          # characters (~512 tokens for bge-m3)
CHUNK_OVERLAP = 200        # characters (~64 tokens)

# ---- Priority entry files ----

PRIORITY_FILENAMES: tuple[str, ...] = (
    "README.md", "README", "readme.md",
    "SKILL.md", "CLAUDE.md", "INDEX.md",
    "CHANGELOG.md", "CONTRIBUTING.md",
)

# ---- W/H/D presets ----

WHD_PRESETS: dict[str, dict[str, tuple[int, int, int | None]]] = {
    "markdown": {
        "peek": (0, 10, 3),
        "skim": (80, 5, 2),
        "deep": (200, 10, 3),
    },
    "code": {
        "peek": (0, 20, 2),
        "skim": (80, 10, 2),
        "deep": (200, 20, 3),
    },
    "json": {
        "peek": (0, 8, 1),
        "skim": (80, 5, 2),
        "deep": (200, 10, 3),
    },
    "directory": {
        "peek": (0, 20, 1),
        "skim": (80, 10, 2),
        "deep": (200, 15, 3),
    },
    "text": {
        "peek": (60, 5, None),
        "skim": (100, 10, None),
        "deep": (200, 30, None),
    },
    "csv": {
        "peek": (30, 3, None),
        "skim": (50, 10, None),
        "deep": (80, 30, None),
    },
    "jsonl": {
        "peek": (80, 3, None),
        "skim": (120, 10, None),
        "deep": (200, 30, None),
    },
}

# ---- Default Milvus collection schema field limits ----

MAX_SOURCE_LEN = 2048
MAX_CHUNK_TEXT_LEN = 65535

# ---- Scan limits ----

MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # skip files larger than 10 MB
