"""Helpers for MFS pipe-mode metadata headers.

Format:
    ::mfs:source=/abs/path
    ::mfs:indexed=true
    ::mfs:hash=<sha256 short>
    ::mfs:lines=1:100        # optional line range

    <file body follows>

Two blank lines between headers and body are also accepted.
"""

from __future__ import annotations

import os
import stat
import sys


def is_pipe() -> bool:
    return not sys.stdout.isatty()


def stdin_has_data() -> bool:
    """Return True only when stdin is a real pipe / redirect the caller likely piped in.

    Four shapes of stdin we care about:
        - tty (terminal): False — nothing to read; search the corpus
        - FIFO (``cmd | mfs``): True — parse headers / warn / Branch D
        - regular file (``mfs < file``): True — treat like a pipe
        - character device / closed FD (e.g. bash subprocess that inherited a
          closed stdin, CI runner, ``/dev/null`` redirect): False — caller did
          not intentionally feed us anything, so fall through to tty semantics

    Under ``click.testing.CliRunner`` the injected stdin is an in-memory wrapper
    with no underlying file descriptor; ``fileno()`` raises
    ``UnsupportedOperation`` (``OSError`` + ``ValueError``). In that case we
    fall back to ``not isatty()`` so tests that pass ``input="..."`` still hit
    the pipe branches.
    """
    try:
        if sys.stdin.isatty():
            return False
    except (ValueError, OSError):
        return False
    try:
        mode = os.fstat(sys.stdin.fileno()).st_mode
    except (ValueError, OSError, AttributeError):
        # No real FD (CliRunner) — stick with the legacy heuristic so the
        # existing pipe-branch tests keep working.
        return True
    return stat.S_ISFIFO(mode) or stat.S_ISREG(mode)


def format_mfs_headers(
    source: str,
    indexed: bool,
    file_hash: str,
    lines: str | None = None,
    converted_from: str | None = None,
) -> str:
    parts = [
        f"::mfs:source={source}",
        f"::mfs:indexed={'true' if indexed else 'false'}",
    ]
    # Only emit a hash line for indexed files — a dangling hash on an
    # unindexed file would make it look retrievable to downstream consumers.
    if file_hash:
        parts.append(f"::mfs:hash={file_hash}")
    if lines:
        parts.append(f"::mfs:lines={lines}")
    if converted_from:
        parts.append(f"::mfs:converted-from={converted_from}")
    return "\n".join(parts) + "\n\n"


def parse_mfs_headers(text: str) -> tuple[dict[str, str] | None, str]:
    """Strip and parse ``::mfs:`` headers at the top of `text`."""
    headers: dict[str, str] = {}
    lines = text.splitlines()
    body_start = 0
    seen = False
    for i, line in enumerate(lines):
        if line.startswith("::mfs:"):
            key, _, value = line[len("::mfs:"):].partition("=")
            headers[key.strip()] = value.strip()
            body_start = i + 1
            seen = True
            continue
        if seen and not line.strip():
            body_start = i + 1
            break
        if seen:
            break
        # No header yet → the whole text is body
        break
    if not headers:
        return None, text
    return headers, "\n".join(lines[body_start:])
