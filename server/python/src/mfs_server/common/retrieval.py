"""Retrieval helpers: Milvus filter expr builder + result envelope (design/03 §11,
06 §7). search modes hybrid/semantic/keyword are dispatched in engine.search.
"""
from __future__ import annotations

from typing import Optional


def _lit(v: str) -> str:
    """Escape a value for a double-quoted Milvus expr string literal. connector_uri /
    object_prefix derive from user paths, so an unescaped `"` or `\\` could break out
    of the literal and bypass namespace/connector scoping (cross-tenant leak)."""
    return str(v).replace("\\", "\\\\").replace('"', '\\"')


def build_filter(namespace_id: str, connector_uri: Optional[str] = None,
                 object_prefix: Optional[str] = None, chunk_kinds: Optional[list[str]] = None) -> str:
    parts = [f'namespace_id == "{_lit(namespace_id)}"']
    if connector_uri:
        parts.append(f'connector_uri == "{_lit(connector_uri)}"')
    if object_prefix:
        parts.append(f'object_uri like "{_lit(object_prefix)}%"')
    if chunk_kinds:
        kinds = ", ".join(f'"{_lit(k)}"' for k in chunk_kinds)
        parts.append(f"chunk_kind in [{kinds}]")
    return " and ".join(parts)


def to_envelope(hit: dict) -> dict:
    """Milvus hit -> stable envelope (design/03 §11)."""
    e = hit.get("entity", hit)
    return {
        "source": e.get("object_uri"),
        "lines": e.get("lines"),
        "content": e.get("content"),
        "score": hit.get("distance"),
        "locator": e.get("locator"),
        "metadata": {
            "kind": "search",
            "chunk_kind": e.get("chunk_kind"),
            "fields": e.get("metadata") or {},
        },
    }


def collapse_by_object(envelopes: list[dict]) -> list[dict]:
    seen: set = set()
    out = []
    for e in envelopes:
        s = e["source"]
        if s not in seen:
            seen.add(s)
            out.append(e)
    return out
