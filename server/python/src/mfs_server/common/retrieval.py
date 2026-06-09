"""Retrieval helpers: Milvus filter expr builder + result envelope. search modes
hybrid/semantic/keyword are dispatched in engine.search.
"""

from __future__ import annotations

import json
from typing import Optional


def _lit(v: str) -> str:
    """Escape a value for a double-quoted Milvus expr string literal. connector_uri /
    object_prefix derive from user paths, so an unescaped `"` or `\\` could break out
    of the literal and bypass namespace/connector scoping (cross-tenant leak).
    Control characters also need escaping so POSIX filenames containing newlines
    or tabs do not produce invalid Milvus expressions."""
    out: list[str] = []
    for ch in str(v):
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "".join(out)


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
        # Scope to the object ITSELF or its SUBTREE, anchored on a PATH-COMPONENT boundary:
        # scoping to `.../src` must NOT over-match a sibling `.../src-old`. A raw
        # startswith(prefix) byte range would (`.../src-old` >= `.../src`), so we anchor the
        # subtree range on prefix + "/" and add an exact-match arm for a file/object scope.
        # (Range, not Milvus LIKE, whose '_'/'%' are wildcards -> wildcard-free.)
        base = object_prefix.rstrip("/")
        sub = base + "/"
        hi = sub + "\U0010ffff"
        parts.append(
            f'(object_uri == "{_lit(base)}" or '
            f'(object_uri >= "{_lit(sub)}" and object_uri < "{_lit(hi)}"))'
        )
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


# Locator keys that identify a sub-chunk WITHIN one object/record, not the
# object/record itself: `lines` (a document body chunk's line range), `chunk_index`
# / `msg_range` (the force-split / message-boundary sub-pieces of one record or thread).
# Collapsing strips these so a long file / issue / thread split across many chunks
# occupies a single result slot, while distinct records / threads (different PK or
# group_by value in the locator) stay separate.
_SUBCHUNK_LOCATOR_KEYS = frozenset({"lines", "chunk_index", "msg_range"})


def _collapse_key(env: dict):
    """Identity of the object-or-record an envelope belongs to: its source plus the
    locator with the per-chunk sub-identity keys removed. A document collapses to its
    source (only `lines` differs between its chunks); a record_collection / message_stream
    collapses per record / thread (the PK / group_by survives in the locator)."""
    loc = env.get("locator")
    if not isinstance(loc, dict):
        return (env.get("source"), None)
    rest = tuple(
        sorted(
            (k, json.dumps(v, sort_keys=True, default=str))
            for k, v in loc.items()
            if k not in _SUBCHUNK_LOCATOR_KEYS
        )
    )
    return (env.get("source"), rest)


def collapse_results(envelopes: list[dict]) -> list[dict]:
    """Keep only the top-ranked hit per object/record so one long file/issue/thread can't
    monopolize the result window (its chunks share a _collapse_key)."""
    seen: set = set()
    out = []
    for e in envelopes:
        k = _collapse_key(e)
        if k not in seen:
            seen.add(k)
            out.append(e)
    return out
