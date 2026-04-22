"""File scanner: walks directories, applies ignore rules, computes file diffs."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

from .. import constants as C
from ..config import Config, IndexingConfig


@dataclass
class FileInfo:
    path: Path
    mtime: float
    size: int
    extension: str


@dataclass
class SyncDiff:
    added: list[FileInfo] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    modified: list[FileInfo] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)


class Scanner:
    """Scans directories and computes sync diff against Milvus state."""

    def __init__(self, config: Config, extra_excludes: list[str] | None = None):
        self._config = config
        self._indexing: IndexingConfig = config.indexing
        self._extra_excludes = list(extra_excludes or [])
        self._gitignore_patterns: list[str] = []
        self._mfsignore_patterns: list[str] = []

    # ---------------------------------------------------------------- public

    def scan(self, paths: list[Path]) -> list[FileInfo]:
        """Scan paths recursively, apply classification and ignore rules.

        Returns all files that are classified as "indexed" (eligible for embedding).
        """
        results: list[FileInfo] = []
        seen: set[Path] = set()
        for p in paths:
            p = p.resolve()
            if p.is_file():
                # For a single file, we still respect classification.
                info = self._file_info_if_indexed(p)
                if info and info.path not in seen:
                    results.append(info)
                    seen.add(info.path)
                continue
            if not p.exists():
                continue
            self._load_ignore_rules(p)
            for f in self._walk_dir(p):
                if f.path not in seen:
                    results.append(f)
                    seen.add(f.path)
        return results

    def compute_diff(
        self,
        all_files: list[FileInfo],
        indexed_files: dict[str, str],
        last_sync_time: float | None,
    ) -> SyncDiff:
        """Compare disk state against Milvus state.

        - added: on disk but not in Milvus
        - deleted: in Milvus but not on disk (uses the complete file list)
        - modified: mtime > last_sync_time and hash differs from Milvus
        """
        diff = SyncDiff()
        current_sources = {str(f.path) for f in all_files}
        indexed_sources = set(indexed_files.keys())

        # Deletion — must use full file list
        diff.deleted = sorted(indexed_sources - current_sources)

        for f in all_files:
            src = str(f.path)
            if src not in indexed_sources:
                diff.added.append(f)
                continue
            # mtime fast-path: trust unchanged mtime
            if last_sync_time is not None and f.mtime <= last_sync_time:
                diff.unchanged.append(src)
                continue
            try:
                file_hash = self.compute_file_hash(f.path)
            except OSError:
                diff.unchanged.append(src)
                continue
            if file_hash != indexed_files.get(src):
                diff.modified.append(f)
            else:
                diff.unchanged.append(src)
        return diff

    def compute_file_hash(self, path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()

    def classify_file(self, path: Path) -> str:
        name = path.name
        if name in C.IGNORED_FILENAMES:
            return "ignored"
        ext = path.suffix.lower()
        # User config overrides take priority
        if ext in {e.lower() for e in self._indexing.exclude_extensions}:
            return "ignored"
        if ext in {e.lower() for e in self._indexing.include_extensions}:
            return "indexed"
        if ext in C.IGNORED_EXTENSIONS:
            return "ignored"
        if ext in C.INDEXED_EXTENSIONS:
            return "indexed"
        if ext in C.NOT_INDEXED_EXTENSIONS:
            return "not_indexed"
        return "ignored"

    # ---------------------------------------------------------------- ignore

    def _load_ignore_rules(self, root: Path) -> None:
        # Reset on every root so different projects don't bleed rules.
        self._gitignore_patterns = _read_ignore_file(root / ".gitignore")
        self._mfsignore_patterns = _read_ignore_file(root / ".mfsignore")

    def _is_ignored_by_rules(self, path: Path, root: Path) -> bool:
        try:
            rel = path.relative_to(root)
        except ValueError:
            return False
        rel_str = str(rel).replace(os.sep, "/")
        rules = self._gitignore_patterns + self._mfsignore_patterns + self._extra_excludes
        for pat in rules:
            pat = pat.strip()
            if not pat or pat.startswith("#"):
                continue
            if pat.endswith("/"):
                pat = pat.rstrip("/")
                if fnmatch(rel_str, pat) or fnmatch(rel_str, pat + "/*") or rel_str.startswith(pat + "/"):
                    return True
                continue
            if fnmatch(rel_str, pat) or fnmatch(path.name, pat):
                return True
            # Match nested (any directory prefix)
            if fnmatch(rel_str, f"**/{pat}"):
                return True
        return False

    # ---------------------------------------------------------------- walk

    def _walk_dir(self, root: Path):
        """Yield FileInfo for indexed files under `root`."""
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dp = Path(dirpath)
            # Prune ignored directories
            dirnames[:] = [
                d for d in dirnames
                if d not in C.IGNORED_DIRNAMES
                and not self._is_ignored_by_rules(dp / d, root)
            ]
            for fn in filenames:
                fp = dp / fn
                if self._is_ignored_by_rules(fp, root):
                    continue
                info = self._file_info_if_indexed(fp)
                if info is not None:
                    yield info

    def _file_info_if_indexed(self, path: Path) -> FileInfo | None:
        try:
            st = path.stat()
        except OSError:
            return None
        if st.st_size > C.MAX_FILE_SIZE_BYTES:
            return None
        cls = self.classify_file(path)
        if cls != "indexed":
            return None
        return FileInfo(
            path=path.resolve(),
            mtime=st.st_mtime,
            size=st.st_size,
            extension=path.suffix.lower(),
        )


def _read_ignore_file(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    return [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
