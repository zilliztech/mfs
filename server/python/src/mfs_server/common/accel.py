"""Hot-path acceleration with a transparent native/pure-Python split (design/10 §1).

The Rust extension `mfs_server_rs` (built from server-rs/ via maturin) accelerates
directory scan, linear grep and JSONL scanning. It is optional: if the wheel isn't
installed, these helpers fall back to equivalent pure-Python implementations so the
server behaves identically (just slower on big inputs). `HAVE_NATIVE` lets callers /
tests report which path is active.
"""
from __future__ import annotations

import json
import os
import re

try:
    import mfs_server_rs as _rs  # type: ignore
    HAVE_NATIVE = True
except ImportError:  # pragma: no cover - exercised on systems without the wheel
    _rs = None
    HAVE_NATIVE = False


def scan_dir(root: str, ignore_substrings: list[str] | None = None) -> list[tuple[str, int, int]]:
    """Recursive walk -> list of (relpath '/foo/bar', size_bytes, mtime_ns)."""
    ignore = ignore_substrings or []
    if HAVE_NATIVE:
        return _rs.scan_dir(root, ignore)
    out: list[tuple[str, int, int]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if not any(s in os.path.join(dirpath, d) for s in ignore)]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            if any(s in full for s in ignore):
                continue
            try:
                st = os.stat(full)
            except OSError:
                continue
            rel = "/" + os.path.relpath(full, root).replace("\\", "/")
            out.append((rel, st.st_size, int(st.st_mtime * 1e9)))
    return out


def linear_grep_file(path: str, pattern: str, case_insensitive: bool = False,
                     regex: bool = False, max_matches: int = 1000) -> list[tuple[int, str]]:
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
                hit = (needle in (line.lower() if case_insensitive else line))
            if hit:
                out.append((i, line))
                if len(out) >= max_matches:
                    break
    return out


def jsonl_record_count(path: str) -> int:
    if HAVE_NATIVE:
        return _rs.jsonl_record_count(path)
    n = 0
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.strip():
                n += 1
    return n


def jsonl_field_texts(path: str, fields: list[str], max_records: int = 1_000_000) -> list[str]:
    if HAVE_NATIVE:
        return _rs.jsonl_field_texts(path, fields, max_records)
    out: list[str] = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                val = json.loads(line)
            except ValueError:
                continue
            parts = []
            for fld in fields:
                if fld in val and val[fld] is not None:
                    v = val[fld]
                    parts.append(f"{fld}: {v if isinstance(v, str) else json.dumps(v)}")
            out.append("\n".join(parts))
            if len(out) >= max_records:
                break
    return out
