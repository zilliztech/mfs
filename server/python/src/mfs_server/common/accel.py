"""Hot-path acceleration with a transparent native/pure-Python split.

The Rust extension `mfs_server_rs` (built from server-rs/ via maturin) accelerates the
gitignore directory walk, parallel content hashing, linear grep and tail. It is optional:
if the wheel isn't installed, these helpers fall back to equivalent pure-Python
implementations so the server behaves identically (just slower on big inputs).
`HAVE_NATIVE` lets callers / tests report which path is active.
"""

from __future__ import annotations

import os
import re

try:
    import mfs_server_rs as _rs  # type: ignore

    HAVE_NATIVE = True
except ImportError:  # pragma: no cover - exercised on systems without the wheel
    _rs = None
    HAVE_NATIVE = False


def linear_grep_file(
    path: str,
    pattern: str,
    case_insensitive: bool = False,
    regex: bool = False,
    max_matches: int = 1000,
) -> list[tuple[int, str]]:
    """Streaming grep over a file -> list of (1-based line_no, line)."""
    if HAVE_NATIVE:
        return _rs.linear_grep_file(path, pattern, case_insensitive, regex, max_matches)
    out: list[tuple[int, str]] = []
    if regex:
        rx = re.compile(pattern, re.IGNORECASE if case_insensitive else 0)
    else:
        needle = pattern.lower() if case_insensitive else pattern
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f, 1):
            line = line.rstrip("\n")
            if regex:
                hit = rx.search(line) is not None
            else:
                hit = needle in (line.lower() if case_insensitive else line)
            if hit:
                out.append((i, line))
                if len(out) >= max_matches:
                    break
    return out


def _walk_onerror(e: OSError):
    raise e


def walk_tree(root: str, patterns: list[str]) -> list[tuple[str, int, int, int]]:
    """Recursive walk applying gitignore-semantics `patterns` (gitwildmatch lines). Returns
    (relpath '/foo', size, mtime_ns, inode) per non-ignored file; ignored dirs are pruned.
    The pure-Python fallback is exactly os.walk + pathspec (the connector's prior behavior),
    so the native path is parity-checked against it."""
    if HAVE_NATIVE:
        return _rs.walk_tree(root, patterns or [])
    import pathspec

    spec = pathspec.PathSpec.from_lines("gitwildmatch", patterns or [])
    out: list[tuple[str, int, int, int]] = []
    for dirpath, dirnames, filenames in os.walk(root, onerror=_walk_onerror):
        kept = []
        for d in dirnames:
            rel = os.path.relpath(os.path.join(dirpath, d), root).replace(os.sep, "/") + "/"
            if not spec.match_file(rel):
                kept.append(d)
        dirnames[:] = kept
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if spec.match_file(rel):
                continue
            st = os.stat(full)
            out.append(("/" + rel, st.st_size, st.st_mtime_ns, st.st_ino))
    return out


def sha1_files(paths: list[str]) -> dict[str, str | None]:
    """Content sha1 (hex) of each path, in parallel natively (GIL released). Unreadable -> None."""
    if HAVE_NATIVE:
        return dict(_rs.sha1_files(list(paths)))
    import hashlib

    out: dict[str, str | None] = {}
    for p in paths:
        try:
            h = hashlib.sha1()
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 16), b""):
                    h.update(chunk)
            out[p] = h.hexdigest()
        except OSError:
            out[p] = None
    return out


def tail_lines(path: str, n: int = 20) -> list[str]:
    """Last n lines of a file, read backward from EOF so a huge file is never fully read
    in. Returns lines oldest->newest, without trailing '\\n'."""
    if n <= 0:
        return []
    if HAVE_NATIVE:
        return _rs.tail_lines(path, n)
    buf = b""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        chunk = 65536
        while pos > 0 and buf.count(b"\n") <= n:
            read = min(chunk, pos)
            pos -= read
            f.seek(pos)
            buf = f.read(read) + buf
    text = buf.decode("utf-8", errors="replace")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    return lines[-n:]
