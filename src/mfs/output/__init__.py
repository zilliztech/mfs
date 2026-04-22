"""Presentation layer: terminal formatting and pipe-mode metadata."""

from __future__ import annotations

from .display import (
    error,
    format_grep_results,
    format_ls,
    format_search_results,
    format_status,
    format_tree,
    warn,
)
from .pipe import format_mfs_headers, is_pipe, parse_mfs_headers, stdin_has_data

__all__ = [
    "error",
    "format_grep_results",
    "format_ls",
    "format_mfs_headers",
    "format_search_results",
    "format_status",
    "format_tree",
    "is_pipe",
    "parse_mfs_headers",
    "stdin_has_data",
    "warn",
]
