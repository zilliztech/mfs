"""Shared ANSI/rich color constants for all CLI outputs.

Centralizing these so search/grep/ls/tree/status share the same visual
vocabulary. Agents and downstream parsers benefit from a consistent palette.

Use rich color names so they degrade gracefully in non-color terminals.
"""

from __future__ import annotations

PATH = "cyan"              # file paths and source identifiers
LINE = "yellow"            # line numbers and L1-3 ranges
SCORE = "dim white"        # search scores, secondary metadata
META = "dim yellow"        # tags like [indexed], [stale], [dir]
INDEX = "bold"             # [N] result numbering
HEADING = "bold cyan"      # markdown headings in cat output
MATCH = "bold red"         # matched substring in grep
WARN = "yellow"
ERROR = "bold red"
NOTE = "dim"               # note: ... lines


__all__ = [
    "PATH", "LINE", "SCORE", "META", "INDEX",
    "HEADING", "MATCH", "WARN", "ERROR", "NOTE",
]
