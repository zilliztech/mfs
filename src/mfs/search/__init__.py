"""Search and browse: hybrid search, grep, W/H/D density, directory summaries."""

from __future__ import annotations

from .density import (
    DensityParams,
    density_view_for_path,
    detect_density_type,
    extract_density_view,
    resolve_density,
)
from .searcher import GrepMatch, Searcher, SearchMode
from .summary import (
    DirSummary,
    aggregate_dir_summary,
    build_dir_summary_records,
    extract_file_summary,
    sort_by_priority,
)

__all__ = [
    "DensityParams",
    "DirSummary",
    "GrepMatch",
    "SearchMode",
    "Searcher",
    "aggregate_dir_summary",
    "build_dir_summary_records",
    "density_view_for_path",
    "detect_density_type",
    "extract_density_view",
    "extract_file_summary",
    "resolve_density",
    "sort_by_priority",
]
