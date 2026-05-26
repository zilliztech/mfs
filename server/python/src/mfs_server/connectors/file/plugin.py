"""File connector — local (shared-fs) mode (design/04 §5.5, §5.7; 09 File).

Server reads the real directory directly. CS upload-flow mode added in a later phase.
Method `path` args are root-relative (leading '/'); framework joins with the connector
root to form the full URI. sync() does stat-first lazy hashing against file_state +
inode/sha1 rename pairing, and declares full enumeration each run.
"""
from __future__ import annotations

import hashlib
import mimetypes
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pathspec

from ..base import (
    Capabilities,
    ConnectorPlugin,
    Entry,
    ObjectChange,
    ObjectKind,
    PathStat,
    Range,
    SyncOptions,
)

DEFAULT_IGNORE = [
    ".git/", ".hg/", ".svn/", "node_modules/", "__pycache__/", ".venv/", "venv/",
    ".mypy_cache/", ".pytest_cache/", ".ruff_cache/", "dist/", "build/", ".idea/", ".vscode/",
    "*.pyc", "*.pyo", "*.so", "*.o", "*.a", "*.dll", "*.dylib", "*.exe", "*.class", "*.jar",
    "*.zip", "*.tar", "*.gz", "*.tgz", "*.bz2", "*.7z", "*.rar",
    "*.mp4", "*.mov", "*.avi", "*.mkv", "*.mp3", "*.wav", "*.flac",
    ".DS_Store", "*.swp", "*.lock",
]

CODE_EXT = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".c", ".h", ".cpp", ".hpp",
    ".cc", ".rb", ".php", ".swift", ".kt", ".scala", ".sh", ".bash", ".sql", ".lua", ".r",
}
DOC_EXT = {".md", ".markdown", ".rst", ".txt", ".pdf", ".docx", ".doc", ".pptx", ".html", ".htm"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".tiff"}
TEXTBLOB_EXT = {".json", ".csv", ".tsv", ".log", ".jsonl", ".ndjson", ".yaml", ".yml", ".toml", ".ini"}


@dataclass
class FileConfig:
    root: str                # real absolute directory (server-side scope / upload staging)
    client_id: str = "local"
    upload_mode: bool = False    # CS upload: index file_state 'staged' rows, no physical re-scan


class FilePlugin(ConnectorPlugin):
    NAME = "file"
    URI_SCHEME = "file"
    DISPLAY_NAME = "Local Files"
    PROMPT = "Local filesystem tree under the connector root. Real files with original names/extensions."
    CAPABILITIES = Capabilities(
        manual_sync=True, watch=True, cursor_kind=None, full_scan=True,
        delete_detection="full_scan", grep_pushdown=False, search_pushdown=False, paged_cat=True,
    )
    CONFIG_SCHEMA = FileConfig

    # engine/tests inject: self.file_state = FileStateStore(...)
    file_state = None

    @property
    def root(self) -> Path:
        return Path(self.config.root)

    def _real(self, path: str) -> Path:
        return self.root / path.lstrip("/")

    def _rel(self, real: Path) -> str:
        return "/" + str(real.relative_to(self.root)).replace(os.sep, "/")

    def _load_ignore(self) -> pathspec.PathSpec:
        lines = list(DEFAULT_IGNORE)
        for fname in (".gitignore", ".mfsignore"):
            f = self.root / fname
            if f.is_file():
                lines += f.read_text(errors="ignore").splitlines()
        return pathspec.PathSpec.from_lines("gitwildmatch", lines)

    # --- object_kind ---
    def object_kind_of(self, path: str) -> ObjectKind:
        real = self._real(path)
        if real.is_dir():
            return "directory"
        ext = real.suffix.lower()
        if ext in CODE_EXT:
            return "code"
        if ext in DOC_EXT:
            return "document"
        if ext in IMAGE_EXT:
            return "image"
        if ext in TEXTBLOB_EXT:
            return "text_blob"
        return "binary"

    def _media_type(self, real: Path) -> Optional[str]:
        ext = real.suffix.lower()
        special = {".md": "text/markdown", ".jsonl": "application/x-ndjson",
                   ".ndjson": "application/x-ndjson", ".py": "text/x-python", ".toml": "application/toml"}
        if ext in special:
            return special[ext]
        mt, _ = mimetypes.guess_type(str(real))
        return mt

    # --- stat / list ---
    async def stat(self, path: str) -> PathStat:
        real = self._real(path)
        if not real.exists():
            raise FileNotFoundError(path)
        if real.is_dir():
            return PathStat(path=path, type="dir")
        st = real.stat()
        return PathStat(path=path, type="file", media_type=self._media_type(real),
                        size_hint=st.st_size, fingerprint=f"{st.st_size}:{st.st_mtime_ns}",
                        extra={"inode": st.st_ino})

    async def list(self, path: str) -> list[Entry]:
        real = self._real(path)
        if not real.is_dir():
            raise NotADirectoryError(path)
        spec = self._load_ignore()
        entries: list[Entry] = []
        for child in sorted(real.iterdir(), key=lambda p: p.name):
            rel = str(child.relative_to(self.root)).replace(os.sep, "/")
            test = rel + "/" if child.is_dir() else rel
            if spec.match_file(test):
                continue
            if child.is_dir():
                entries.append(Entry(name=child.name, type="dir"))
            else:
                stt = child.stat()
                entries.append(Entry(name=child.name, type="file",
                                     media_type=self._media_type(child), size_hint=stt.st_size))
        return entries

    # --- read ---
    async def read(self, path: str, range: Optional[Range] = None) -> AsyncIterator[bytes]:
        real = self._real(path)
        if range is None:
            with open(real, "rb") as f:
                while chunk := f.read(65536):
                    yield chunk
        else:
            # line range for text
            lines = real.read_text(errors="replace").splitlines(keepends=True)
            for line in lines[range.start:range.end]:
                yield line.encode()

    # --- fingerprint: content sha1 (accurate; stat uses size:mtime for fast check) ---
    async def fingerprint(self, path: str) -> Optional[str]:
        real = self._real(path)
        if not real.is_file():
            return None
        return self._sha1(real)

    @staticmethod
    def _sha1(real: Path) -> str:
        h = hashlib.sha1()
        with open(real, "rb") as f:
            while chunk := f.read(1 << 20):
                h.update(chunk)
        return h.hexdigest()

    def _scan(self, spec: pathspec.PathSpec) -> dict[str, os.stat_result]:
        """Walk root, apply ignore, return {relpath: stat}. Raises on IO/permission
        error (design/04 §5.5 step 1: enumerate completely or raise)."""
        out: dict[str, os.stat_result] = {}
        for dirpath, dirnames, filenames in os.walk(self.root, onerror=_raise):
            # prune ignored dirs in-place
            kept = []
            for d in dirnames:
                rel = str((Path(dirpath) / d).relative_to(self.root)).replace(os.sep, "/") + "/"
                if not spec.match_file(rel):
                    kept.append(d)
            dirnames[:] = kept
            for fn in filenames:
                real = Path(dirpath) / fn
                rel = str(real.relative_to(self.root)).replace(os.sep, "/")
                if spec.match_file(rel):
                    continue
                out["/" + rel] = real.stat()
        return out

    # --- sync (core: stat-first + rename pairing) ---
    async def sync(self, opts: SyncOptions) -> AsyncIterator[ObjectChange]:
        assert self.file_state is not None, "file_state not injected"

        # CS upload mode (design/02 §4.2 ⑤): the manifest/upload commit already wrote
        # file_state ('staged' = needs index, 'deleted' = needs removal), and the bytes are
        # in the staging dir. Don't physically re-scan (client mtime != staging mtime would
        # cause needless re-hashing) — just yield the staged/deleted rows.
        if getattr(self.config, "upload_mode", False):
            self.ctx.declare_enumeration("explicit_only")
            for row in await self.file_state.all_rows():
                if row["status"] == "deleted":
                    yield ObjectChange(uri=row["path"], kind="deleted")
                elif row["status"] == "staged":
                    if row["renamed_from"]:
                        yield ObjectChange(uri=row["path"], kind="renamed", old_uri=row["renamed_from"])
                    else:
                        yield ObjectChange(uri=row["path"], kind="added")
            return

        self.ctx.declare_enumeration("full")        # file scans whole tree every time

        spec = self._load_ignore()
        current = self._scan(spec)
        prev_paths = await self.file_state.all_paths()

        added: dict[str, tuple] = {}     # path -> (size, mtime_ns, inode, sha1)
        modified: dict[str, tuple] = {}
        for path, st in current.items():
            fs = await self.file_state.get(path)
            if fs and not opts.full and fs["size"] == st.st_size and fs["mtime_ns"] == st.st_mtime_ns and fs["status"] == "indexed":
                continue                                  # unchanged
            sha1 = self._sha1(self._real(path))
            if fs and not opts.full and sha1 == fs["sha1"] and fs["status"] == "indexed":
                await self.file_state.update_mtime(path, st.st_mtime_ns)   # mtime-touch only
                continue
            rec = (st.st_size, st.st_mtime_ns, st.st_ino, sha1)
            if fs:
                modified[path] = rec
            else:
                added[path] = rec

        deleted = prev_paths - set(current.keys())

        # rename pairing: added x deleted (inode then sha1), design/04 §5.7.2
        deleted_rows = {p: await self.file_state.get(p) for p in deleted}
        del_by_inode = {r["inode"]: p for p, r in deleted_rows.items() if r and r["inode"] is not None}
        del_by_sha1 = {r["sha1"]: p for p, r in deleted_rows.items() if r}
        consumed_deleted: set[str] = set()

        for new_path in sorted(added):
            size, mtime, inode, sha1 = added[new_path]
            old = None
            if inode in del_by_inode:
                cand = del_by_inode[inode]
                if deleted_rows[cand]["size"] == size:
                    old = cand
            if old is None and sha1 in del_by_sha1:
                old = del_by_sha1[sha1]
            if old is not None and old not in consumed_deleted:
                consumed_deleted.add(old)
                await self.file_state.rename(old, new_path)   # staged, renamed_from=old
                await self.file_state.update_mtime(new_path, mtime)
                yield ObjectChange(uri=new_path, kind="renamed", old_uri=old)
            else:
                await self.file_state.upsert(new_path, size, mtime, inode, sha1, status="staged")
                yield ObjectChange(uri=new_path, kind="added")

        for path, (size, mtime, inode, sha1) in modified.items():
            await self.file_state.upsert(path, size, mtime, inode, sha1, status="staged")
            yield ObjectChange(uri=path, kind="modified")

        for path in sorted(deleted - consumed_deleted):
            yield ObjectChange(uri=path, kind="deleted")

    # --- framework callbacks (file_state staged -> indexed / delete) ---
    async def on_object_indexed(self, uri: str) -> None:
        if self.file_state is not None:
            from datetime import datetime, timezone
            await self.file_state.mark_indexed(uri, datetime.now(timezone.utc).isoformat())

    async def on_object_deleted(self, uri: str) -> None:
        if self.file_state is not None:
            await self.file_state.delete(uri)


def _raise(err: OSError) -> None:
    raise err
