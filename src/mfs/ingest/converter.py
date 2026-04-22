"""Binary document -> Markdown conversion with an on-disk cache.

Only PDF is wired up for MVP (via pymupdf4llm). The cache path mirrors the
content hash of the source so repeated indexing of an unchanged file skips
the expensive conversion step.

Cache layout:
    $MFS_HOME/converted/<hash[:2]>/<hash>.md

LRU eviction based on ``cache.max_size_mb`` is deferred — the cache simply
grows until the user clears ``~/.mfs/converted``. This is noted in the
design spec and acceptable for MVP.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..config import ensure_mfs_home


def is_convertible(extension: str) -> bool:
    return extension.lower() == ".pdf"


def _cache_path(file_hash: str) -> Path:
    home = ensure_mfs_home()
    return home / "converted" / file_hash[:2] / f"{file_hash}.md"


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def convert_to_markdown(path: Path) -> str:
    """Return Markdown text for *path*, using the on-disk cache when possible.

    Raises ``RuntimeError`` if the format isn't supported or the converter
    library is missing.
    """
    ext = path.suffix.lower()
    if ext != ".pdf":
        raise RuntimeError(f"no converter registered for {ext}")

    file_hash = _hash_file(path)
    cache = _cache_path(file_hash)
    if cache.exists():
        try:
            return cache.read_text(encoding="utf-8")
        except OSError:
            pass  # fall through and re-convert

    try:
        import pymupdf4llm  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - import guard
        raise RuntimeError(
            "PDF conversion requires pymupdf4llm. "
            "Install with: uv add pymupdf4llm"
        ) from exc

    try:
        markdown = pymupdf4llm.to_markdown(str(path))
    except Exception as exc:
        raise RuntimeError(f"pymupdf4llm failed on {path}: {exc}") from exc

    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(markdown, encoding="utf-8")
    except OSError:
        # Cache is best-effort; ignore write failures (disk full, permissions).
        pass
    return markdown
