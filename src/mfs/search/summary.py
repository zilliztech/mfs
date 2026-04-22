"""Directory summary extraction: rule-based, no LLM.

Files → extract summary via W/H/D (skim preset).
Directories → aggregate child summaries bottom-up.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .. import constants as C
from ..ingest.scanner import Scanner
from ..store import ChunkRecord, MilvusStore
from .density import (
    detect_density_type,
    extract_density_view,
    resolve_density,
)

DIR_SUMMARY_MAX_CHARS = 500


@dataclass
class DirSummary:
    path: str
    text: str
    metadata: dict  # {"file_count": N, "indexed_count": M}


# ------------------------------------------------------------------ file summary


def extract_file_summary(path: Path, content: str | None = None) -> str:
    """Return a short summary for a file using the skim preset.

    Returns an empty string for binary-looking files so callers don't
    display garbage text.
    """
    ext = path.suffix.lower()
    ctype = detect_density_type(ext)
    if content is None:
        # Route convertible formats (PDF) through the converter so the summary
        # reflects real text, not the binary preamble.
        from ..ingest.converter import convert_to_markdown, is_convertible
        if is_convertible(ext):
            try:
                content = convert_to_markdown(path)
                ctype = detect_density_type(".md")
            except RuntimeError:
                return ""
        else:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return ""
    if _looks_binary(content):
        return ""
    params = resolve_density(ctype, preset="skim")
    view = extract_density_view(content, ctype, params)
    # Fall back to first non-empty line
    if not view.strip():
        for line in content.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped[:200]
        return ""
    # Keep summaries compact (single-block)
    return _clamp(view, DIR_SUMMARY_MAX_CHARS)


def _looks_binary(text: str, sample_size: int = 4096) -> bool:
    """Heuristic: treat a file as binary if the first few KB contain NULs
    or a high proportion of non-printable control characters."""
    if not text:
        return False
    sample = text[:sample_size]
    if "\x00" in sample:
        return True
    # Count control chars outside \t \n \r
    control = sum(1 for c in sample if ord(c) < 32 and c not in "\t\n\r")
    return control / max(1, len(sample)) > 0.10


# --------------------------------------------------------- priority sorting


def _priority_key(path: Path) -> tuple:
    """Lower tuple = higher priority."""
    name = path.name
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0
    depth = len(path.parts)
    in_priority = 0 if name in C.PRIORITY_FILENAMES else 1
    is_doc = 0 if path.suffix.lower() in C.MARKDOWN_EXTENSIONS else 1
    # Prefer files over directories at the same level for the "README first" intent
    is_file = 0 if path.is_file() else 1
    # Most-recent-first → negate mtime
    return (in_priority, is_file, depth, is_doc, -mtime, name.lower())


def sort_by_priority(entries: list[Path]) -> list[Path]:
    return sorted(entries, key=_priority_key)


# ------------------------------------------------------------- aggregation


def aggregate_dir_summary(
    dir_path: Path,
    child_entries: list[tuple[str, str, bool]],  # [(name, summary_text, is_dir), ...]
    file_count: int,
    indexed_count: int,
    max_chars: int = DIR_SUMMARY_MAX_CHARS,
) -> DirSummary:
    """Aggregate children into a single directory summary string."""
    # Sort children by priority: dir_path-local paths so the real priority key applies
    sorted_entries = sorted(
        child_entries,
        key=lambda e: _priority_key(dir_path / e[0]),
    )

    parts: list[str] = []
    remaining = max_chars
    for name, text, is_dir in sorted_entries:
        if remaining <= 0:
            break
        prefix = f"{name}/" if is_dir else name
        piece = text.replace("\n", " ").strip()
        if not piece:
            entry = f"- {prefix}"
        else:
            # Each entry gets a share of the remaining budget; cap individual entries.
            snippet = piece[: min(len(piece), max(40, remaining // 2))]
            if len(piece) > len(snippet):
                snippet = snippet.rstrip() + "…"
            entry = f"- {prefix}: {snippet}"
        parts.append(entry)
        remaining -= len(entry) + 1

    text = "\n".join(parts)
    text = _clamp(text, max_chars)
    return DirSummary(
        path=str(dir_path),
        text=text,
        metadata={"file_count": file_count, "indexed_count": indexed_count},
    )


# ------------------------------------------------------------- helpers


def _clamp(s: str, max_chars: int) -> str:
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 1].rstrip() + "…"


# ------------------------------------------------------------- build + persist


def build_dir_summary_records(
    roots: list[Path],
    scanner: Scanner,
    store: MilvusStore,
    account_id: str,
    embedder_dim: int,
) -> list[ChunkRecord]:
    """Walk `roots` bottom-up, build directory summary ChunkRecords.

    The records use `is_dir=True`, `chunk_index=0`, `dense_vector=zero` (the
    worker will never touch them), `embed_status="complete"`. This is adequate
    for hybrid/keyword search on dir summaries; a future iteration could embed
    the summary text for semantic ranking.
    """
    # Collect unique dirs from all indexed files under each root
    dirs: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            dirs.add(root.parent)
            continue
        for f in scanner.scan([root]):
            p = f.path.parent
            # Include all ancestors up to (and including) the root
            while True:
                dirs.add(p)
                if p == root or p == p.parent:
                    break
                p = p.parent

    records: list[ChunkRecord] = []
    # Bottom-up by depth
    for dir_path in sorted(dirs, key=lambda p: len(p.parts), reverse=True):
        entry_texts: list[tuple[str, str, bool]] = []
        file_count = 0
        indexed_count = 0
        try:
            children = list(dir_path.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_dir():
                if child not in dirs:
                    continue
                # Pull child's summary from Milvus if already written
                existing = store.get_dir_summary(str(child))
                if existing is not None:
                    entry_texts.append((child.name, existing.chunk_text, True))
                continue
            file_count += 1
            cls = scanner.classify_file(child)
            if cls != "indexed":
                continue
            indexed_count += 1
            try:
                summary_text = extract_file_summary(child)
            except Exception:
                summary_text = ""
            entry_texts.append((child.name, summary_text, False))

        summary = aggregate_dir_summary(
            dir_path,
            child_entries=entry_texts,
            file_count=file_count,
            indexed_count=indexed_count,
        )
        chunk_id = _dir_chunk_id(str(dir_path), summary.text)
        records.append(
            ChunkRecord(
                id=chunk_id,
                source=str(dir_path),
                parent_dir=str(dir_path.parent),
                chunk_index=0,
                start_line=0,
                end_line=0,
                chunk_text=summary.text or f"[directory: {dir_path.name}]",
                dense_vector=[0.0] * embedder_dim,
                content_type=C.CONTENT_TYPE_DIRECTORY,
                file_hash="",
                is_dir=True,
                embed_status="complete",
                metadata=summary.metadata,
                account_id=account_id,
            )
        )
        # Flush this record so deeper ancestors can read it on their iteration
        store.insert_chunks([records[-1]])
    return records


def _dir_chunk_id(dir_path: str, summary_text: str) -> str:
    import hashlib
    raw = f"__dir__:{dir_path}:{summary_text}"
    return "d" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:15]
