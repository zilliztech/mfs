"""Unified JSON envelope for hit-list commands.

``search``, ``grep``, ``ls`` and ``tree`` all describe "things found at a
path"; giving them a single envelope lets agents and downstream tools
parse all four with one schema. ``status`` keeps its own shape (it's
about global state, not a hit list).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Hit:
    source: str                               # absolute path
    lines: tuple[int, int] | None             # (start, end); None when not line-bound
    content: str                              # excerpt / summary / matched text
    score: float | None = None                # search score, None for grep/ls/tree
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Convert tuple -> list for JSON compatibility.
        if d["lines"] is not None:
            d["lines"] = list(d["lines"])
        return d


__all__ = ["Hit"]
