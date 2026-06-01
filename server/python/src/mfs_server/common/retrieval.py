"""Retrieval helpers: Milvus filter expr builder + result envelope. search modes
hybrid/semantic/keyword are dispatched in engine.search.
"""

from __future__ import annotations

from typing import Optional


def _lit(v: str) -> str:
    """Escape a value for a double-quoted Milvus expr string literal. connector_uri /
    object_prefix derive from user paths, so an unescaped `"` or `\\` could break out
    of the literal and bypass namespace/connector scoping (cross-tenant leak)."""
    return str(v).replace("\\", "\\\\").replace('"', '\\"')


def build_filter(
    namespace_id: str,
    connector_uri: Optional[str] = None,
    object_prefix: Optional[str] = None,
    chunk_kinds: Optional[list[str]] = None,
) -> str:
    parts = [f'namespace_id == "{_lit(namespace_id)}"']
    if connector_uri:
        parts.append(f'connector_uri == "{_lit(connector_uri)}"')
    if object_prefix:
        # Prefix scope via a byte range, NOT `like "...%"`. Milvus LIKE treats '_' and '%'
        # in the pattern as wildcards, so scoping to a path whose component contains '_'
        # (ubiquitous, e.g. /my_dir) would over-match siblings like /myXdir (verified).
        # [prefix, prefix+U+10FFFF) is an exact, wildcard-free startswith(prefix).
        hi = object_prefix + "\U0010ffff"
        parts.append(f'object_uri >= "{_lit(object_prefix)}" and object_uri < "{_lit(hi)}"')
    if chunk_kinds:
        kinds = ", ".join(f'"{_lit(k)}"' for k in chunk_kinds)
        parts.append(f"chunk_kind in [{kinds}]")
    return " and ".join(parts)


def to_envelope(hit: dict) -> dict:
    """Milvus hit -> stable envelope.

    `locator` carries the per-chunk identity in a single field:
      - body / code / document chunks  -> {"lines": [start, end]}
      - structured rows / msgs / issues -> connector PK dict
      - once-per-object kinds            -> None
    Agents dispatch on what's inside: see `lines` -> `cat --range` (or
    `cat --locator '{"lines":[s,e]}'`); see other keys -> `cat --locator`.
    """
    e = hit.get("entity", hit)
    return {
        "source": e.get("object_uri"),
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
