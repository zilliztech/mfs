"""File connector — local (shared-fs) mode.

Server reads the real directory directly. CS upload-flow mode added in a later phase.
Method `path` args are root-relative (leading '/'); framework joins with the connector
root to form the full URI. sync() does stat-first lazy hashing against file_state +
inode/sha1 rename pairing, and declares full enumeration each run.
"""

from __future__ import annotations

import asyncio
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
    GrepMatch,
    GrepOptions,
    HealthStatus,
    ObjectChange,
    ObjectKind,
    PathStat,
    Range,
    SyncOptions,
)

DEFAULT_IGNORE = [
    ".git/",
    ".hg/",
    ".svn/",
    "node_modules/",
    "__pycache__/",
    ".venv/",
    "venv/",
    ".mypy_cache/",
    ".pytest_cache/",
    ".ruff_cache/",
    "dist/",
    "build/",
    ".idea/",
    ".vscode/",
    "*.pyc",
    "*.pyo",
    "*.so",
    "*.o",
    "*.a",
    "*.dll",
    "*.dylib",
    "*.exe",
    "*.class",
    "*.jar",
    "*.zip",
    "*.tar",
    "*.gz",
    "*.tgz",
    "*.bz2",
    "*.7z",
    "*.rar",
    "*.mp4",
    "*.mov",
    "*.avi",
    "*.mkv",
    "*.mp3",
    "*.wav",
    "*.flac",
    ".DS_Store",
    "*.swp",
    "*.lock",
]

CODE_EXT = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".go",
    ".rs",
    ".java",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cc",
    ".rb",
    ".php",
    ".swift",
    ".kt",
    ".scala",
    ".sh",
    ".bash",
    ".sql",
    ".lua",
    ".r",
}
DOC_EXT = {".md", ".markdown", ".rst", ".txt", ".pdf", ".docx", ".doc", ".pptx", ".html", ".htm"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".tiff"}
TEXTBLOB_EXT = {
    ".json",
    ".csv",
    ".tsv",
    ".log",
    ".jsonl",
    ".ndjson",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
}

# --- Progressive-availability priority table (design 02-architecture.md §6.3) ---
# Smaller priority = runs earlier. The buckets are matched in this order; first
# hit wins (so e.g. a tests/README.md still buckets as the README, not tests).
# v0.4 hard-codes the table; users can't tune it — priority only affects perceived
# ordering, never correctness. All basename/top-dir matches are case-insensitive.
_ENTRYPOINT_BASENAMES = {"readme.md", "claude.md", "skill.md", "index.md"}
_CONFIG_MANIFEST_BASENAMES = {
    "pyproject.toml",
    "package.json",
    "cargo.toml",
    "go.mod",
    "requirements.txt",
    "setup.py",
    "setup.cfg",
    "gemfile",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "makefile",
    "cmakelists.txt",
    "tsconfig.json",
    "deno.json",
    "composer.json",
}
_CORE_SRC_TOPDIRS = {"src", "lib", "app"}
_DOCS_TOPDIRS = {"docs", "guides"}
_TESTS_TOPDIRS = {"tests", "test", "__tests__", "fixtures"}
_GENERATED_TOPDIRS = {"dist", "build", "vendor", "node_modules", "target", "out"}


@dataclass
class FileConfig:
    root: str  # real absolute directory (server-side scope / upload staging)
    client_id: str = "local"
    upload_mode: bool = False  # CS upload: index file_state 'staged' rows, no physical re-scan


class FilePlugin(ConnectorPlugin):
    NAME = "file"
    URI_SCHEME = "file"
    DISPLAY_NAME = "Local Files"
    PROMPT = (
        "Local filesystem tree under the connector root. Real files with original names/extensions."
    )
    CAPABILITIES = Capabilities(
        manual_sync=True,
        # No filesystem watcher / daemon is wired anywhere (nothing consumes
        # CAPABILITIES.watch beyond serialization), so advertising watch=True
        # claimed a capability the connector does not actually have. Declare it
        # honestly until an inotify/poll watcher is implemented.
        watch=False,
        cursor_kind=None,
        full_scan=True,
        delete_detection="full_scan",
        grep_pushdown=True,
        search_pushdown=False,
        paged_cat=True,
    )
    CONFIG_SCHEMA = FileConfig

    # engine/tests inject: self.file_state = FileStateStore(...)
    file_state = None

    @property
    def root(self) -> Path:
        return Path(self.config.root)

    async def healthcheck(self) -> HealthStatus:
        # The base default rubber-stamps ok=True; for a LOCAL connector "try-connect"
        # means the root must actually be a readable directory. Without this, `probe`
        # reports ok:true for a missing/non-directory root and lets a doomed `add`
        # proceed (it would then 4xx as connector_unhealthy — keep probe consistent).
        if not self.root.is_dir():
            return HealthStatus(ok=False, detail="connector_unhealthy")
        return HealthStatus(ok=True)

    def _real(self, path: str) -> Path:
        # Resolve and confine to the connector root. The control plane matches connector
        # URIs by string prefix without normalizing, so a relpath like '/../secret.txt'
        # (e.g. file://local/tmp/root/../secret.txt) would otherwise read outside root.
        # This is the single chokepoint for every read (stat/read/list/fingerprint), and
        # resolve() also defeats symlink escapes.
        root = self.root.resolve()
        real = (root / path.lstrip("/")).resolve()
        if real != root and root not in real.parents:
            raise ValueError(f"path_escapes_root: {path}")
        return real

    def _rel(self, real: Path) -> str:
        return "/" + str(real.relative_to(self.root)).replace(os.sep, "/")

    def _ignore_patterns(self) -> list[str]:
        lines = list(DEFAULT_IGNORE)
        for fname in (".gitignore", ".mfsignore"):
            f = self.root / fname
            if f.is_file():
                lines += f.read_text(errors="ignore").splitlines()
        return lines

    def _load_ignore(self) -> pathspec.PathSpec:
        return pathspec.PathSpec.from_lines("gitwildmatch", self._ignore_patterns())

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

    def task_priority(self, change: ObjectChange) -> int:
        """Progressive-availability bias for `mfs add .` — see design §6.3.

        Smaller = earlier. We classify the change URI (root-relative path) into
        the buckets in that section's table: entrypoints (README/CLAUDE/SKILL/
        INDEX) → -350; build manifests → -260; src/lib/app → -220; docs/guides
        → -190; tests/fixtures → +80; dist/build/vendor → +260; everything else
        → 0. Effect: by the time the long tail (tests / generated) is still
        building, the things an agent actually reaches for first are already
        in the index. Basename match wins over top-dir match — a README inside
        tests/ is still a README."""
        rel = change.uri or ""
        parts = [p for p in rel.split("/") if p]
        if not parts:
            return 0
        base = parts[-1].lower()
        if base in _ENTRYPOINT_BASENAMES:
            return -350
        if base in _CONFIG_MANIFEST_BASENAMES:
            return -260
        top = parts[0].lower() if len(parts) > 1 else None
        if top is None:
            return 0
        if top in _CORE_SRC_TOPDIRS:
            return -220
        if top in _DOCS_TOPDIRS:
            return -190
        if top in _TESTS_TOPDIRS:
            return 80
        if top in _GENERATED_TOPDIRS:
            return 260
        return 0

    def _media_type(self, real: Path) -> Optional[str]:
        ext = real.suffix.lower()
        special = {
            ".md": "text/markdown",
            ".jsonl": "application/x-ndjson",
            ".ndjson": "application/x-ndjson",
            ".py": "text/x-python",
            ".toml": "application/toml",
        }
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
        return PathStat(
            path=path,
            type="file",
            media_type=self._media_type(real),
            size_hint=st.st_size,
            fingerprint=f"{st.st_size}:{st.st_mtime_ns}",
            extra={"inode": st.st_ino},
        )

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
                entries.append(
                    Entry(
                        name=child.name,
                        type="file",
                        media_type=self._media_type(child),
                        size_hint=stt.st_size,
                    )
                )
        return entries

    # --- read ---
    async def read(self, path: str, range: Optional[Range] = None) -> AsyncIterator[bytes]:
        real = self._real(path)
        if range is None:
            with open(real, "rb") as f:
                while chunk := f.read(65536):
                    yield chunk
        else:
            # line range [start, end) — stream line-by-line; never read the whole file in
            # (cat --range is the paging escape hatch for large files, so it must not OOM).
            start, end = range.start, range.end
            i = 0
            buf = b""
            with open(real, "rb") as f:
                while chunk := f.read(65536):
                    buf += chunk
                    while (nl := buf.find(b"\n")) >= 0:
                        line, buf = buf[: nl + 1], buf[nl + 1 :]
                        if start <= i < end:
                            yield line
                        i += 1
                        if i >= end:
                            return
                if buf and start <= i < end:  # trailing line without a newline
                    yield buf

    async def grep(
        self, pattern: str, path: str, options: GrepOptions
    ) -> Optional[AsyncIterator[GrepMatch]]:
        """Run exact literal grep against the source files.

        Indexed BM25 is useful recall, but it is not exact grep: analyzers can miss
        punctuation-heavy tokens, and partial objects only store capped chunk text.
        The file connector always has the bytes locally, including upload staging, so
        source-side grep is the correct behavior for exact file matches.
        """
        from ...common import accel

        root = self.root.resolve()
        real = self._real(path)
        if not real.exists():
            raise FileNotFoundError(path)

        if real.is_file():
            relpaths = [
                "/" + str(real.relative_to(root)).replace(os.sep, "/"),
            ]
        elif real.is_dir():
            if real == root:
                relpaths = list(self._scan().keys())
            else:
                base = "/" + str(real.relative_to(root)).replace(os.sep, "/")
                prefix = base.rstrip("/") + "/"
                relpaths = [rel for rel in self._scan().keys() if rel.startswith(prefix)]
        else:
            relpaths = []

        async def gen() -> AsyncIterator[GrepMatch]:
            emitted = 0
            max_matches = 100
            for rel in relpaths:
                if emitted >= max_matches:
                    break
                matches = await asyncio.to_thread(
                    accel.linear_grep_file,
                    str(self._real(rel)),
                    pattern,
                    options.case_insensitive,
                    False,
                    max_matches - emitted,
                )
                for line_no, line in matches:
                    emitted += 1
                    yield GrepMatch(path=rel, line_no=line_no, content=line)

        return gen()

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

    def _scan(self) -> dict[str, tuple[int, int, int]]:
        """Walk root, apply gitignore-semantics ignore, return {relpath: (size, mtime_ns,
        inode)} via the native accelerator (mfs_server_rs) or its pure-Python (os.walk +
        pathspec) fallback. Raises on IO/permission error (enumerate completely or raise)."""
        from ...common import accel

        # A missing / non-directory root (never created, or deleted out from under a
        # synchronous add/estimate enumerate) is a source-health problem, not an internal
        # error. Surface it as a clean `connector_unhealthy` envelope instead of letting the
        # raw OSError bubble up to the generic 500 handler. The upfront check is
        # backend-agnostic (covers both the native and pure-Python walkers); the except guards
        # the narrow TOCTOU race where the root vanishes between the check and the walk.
        if not self.root.is_dir():
            raise ValueError("connector_unhealthy")
        try:
            return {
                rel: (size, mtime_ns, inode)
                for rel, size, mtime_ns, inode in accel.walk_tree(
                    str(self.root), self._ignore_patterns()
                )
            }
        except OSError as e:
            raise ValueError("connector_unhealthy") from e

    # --- sync (core: stat-first + rename pairing) ---
    async def sync(self, opts: SyncOptions) -> AsyncIterator[ObjectChange]:
        assert self.file_state is not None, "file_state not injected"

        # CS upload mode: the manifest/upload commit already wrote
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
                        yield ObjectChange(
                            uri=row["path"], kind="renamed", old_uri=row["renamed_from"]
                        )
                    else:
                        yield ObjectChange(uri=row["path"], kind="added")
                elif opts.full and row["status"] == "indexed":
                    # --force-index: rebuild the whole upload, not just the
                    # rows this bundle touched — re-yield already-indexed staging rows as
                    # modified so a forced re-index actually re-embeds them.
                    yield ObjectChange(uri=row["path"], kind="modified")
            return

        self.ctx.declare_enumeration("full")  # file scans whole tree every time

        from ...common import accel

        current = self._scan()

        # dry_run (estimate pre-flight): enumerate object URIs only — never
        # hash bytes and never touch file_state. Otherwise estimate would sha1 the whole
        # tree AND leak 'staged' rows under its throwaway connector id (no connector row =
        # nothing ever cleans them up). Treat every object as 'added' so the caller can
        # count + sample without any persistent side effect.
        if opts.dry_run:
            for path in current:
                yield ObjectChange(uri=path, kind="added")
            return

        prev_paths = await self.file_state.all_paths()

        added: dict[str, tuple] = {}  # path -> (size, mtime_ns, inode, sha1)
        modified: dict[str, tuple] = {}
        # pass 1: stat-first — only files whose (size, mtime) changed need a content hash
        fsmap: dict[str, dict | None] = {}
        need_hash: list[str] = []
        for path, (size, mtime_ns, inode) in current.items():
            fs = await self.file_state.get(path)
            fsmap[path] = fs
            if (
                fs
                and not opts.full
                and fs["size"] == size
                and fs["mtime_ns"] == mtime_ns
                and fs["status"] == "indexed"
            ):
                continue  # unchanged, skip hashing
            need_hash.append(path)
        # batch the (parallel, GIL-released) content hashing of just the changed files
        hashes = await asyncio.to_thread(accel.sha1_files, [str(self._real(p)) for p in need_hash])
        for path in need_hash:
            size, mtime_ns, inode = current[path]
            fs = fsmap[path]
            sha1 = hashes.get(str(self._real(path)))
            if fs and not opts.full and sha1 == fs["sha1"] and fs["status"] == "indexed":
                await self.file_state.update_mtime(path, mtime_ns)  # mtime-touch only
                continue
            rec = (size, mtime_ns, inode, sha1)
            if fs:
                modified[path] = rec
            else:
                added[path] = rec

        deleted = prev_paths - set(current.keys())

        # rename pairing: added x deleted (inode then sha1)
        deleted_rows = {p: await self.file_state.get(p) for p in deleted}
        del_by_inode = {
            r["inode"]: p for p, r in deleted_rows.items() if r and r["inode"] is not None
        }
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
                await self.file_state.rename(old, new_path)  # staged, renamed_from=old
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
