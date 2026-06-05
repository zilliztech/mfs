"""Record rendering + thread splitting for the structured producers.

These are JSON-record -> chunk-text utilities shared by MessageStreamProducer and
RecordCollectionProducer. They are byte-for-byte the same logic as the originals in
`engine.py` (`_resolve_path` / `_render_record` / `_split_thread` etc.); kept here so
the producers package is self-contained and unit-testable without importing the heavy
engine module. Step 4 unifies the two copies by having engine import from here.
"""

from __future__ import annotations

import re

_PATH_SEG = re.compile(r"^([^\[\]]+)(?:\[([^\]]*)\])?$")


def resolve_path(obj: object, path: str):
    """JSONPath-lite field resolver. Supports:
      a.b           nested dict access
      a[*].b / a[].b  every element's b   -> flattened list
      a[2].b        index
      a[0:5].b      slice                 -> list
    Returns a scalar for single-valued paths, a list for multi-valued ones, None/[]
    when absent. Used for text_fields / metadata_fields / locator_fields."""
    nodes = [obj]
    multi = False
    for seg in path.split("."):
        m = _PATH_SEG.match(seg)
        if not m:
            return None
        key, br = m.group(1), m.group(2)
        nxt = []
        for n in nodes:
            if not isinstance(n, dict) or key not in n:
                continue
            v = n[key]
            if br is None:
                nxt.append(v)
                continue
            if not isinstance(v, list):
                v = [v]
            if br in ("*", ""):
                nxt.extend(v)
                multi = True
            elif ":" in br:
                a, _, b = br.partition(":")
                nxt.extend(v[slice(int(a) if a else None, int(b) if b else None)])
                multi = True
            else:
                idx = int(br)
                if -len(v) <= idx < len(v):
                    nxt.append(v[idx])
        nodes = nxt
    if multi:
        return nodes
    return nodes[0] if nodes else None


def field_values(rec: dict, field: str) -> list[str]:
    """Resolved field as a list of non-empty stringified values."""
    v = resolve_path(rec, field)
    if v is None:
        return []
    items = v if isinstance(v, list) else [v]
    return [str(x) for x in items if x is not None and x != ""]


def field_top_key(field: str) -> str:
    """First JSONPath-lite segment of a text_field — the record key whose PRESENCE
    (not value) decides absent-vs-empty: 'comments[].body' -> 'comments'."""
    return field.split(".", 1)[0].split("[", 1)[0]


class _SafeDict(dict):
    """str.format_map helper: missing keys render as empty string, nested {a[b]}
    lookups still work because str.format reaches into the value."""

    def __init__(self, rec: dict):
        super().__init__(rec)

    def __missing__(self, key):
        return ""


def render_record(rec: dict, text_fields: list[str], render_template: str | None = None) -> str:
    """Render a record into chunk content for embedding.

    Two rendering modes:

    1. **render_template** (chat / message presets): Python str.format applied
       directly to the record. e.g. `"{user}: {text}"` keeps speaker identity inside
       each chunk so the embedding learns who-said-what. Missing keys fall back to an
       empty string instead of raising KeyError.

    2. **labeled text_fields** (default for structured / record_collection presets):
       JSONPath-lite walk of each field, joined with `field: value` lines. Multi-valued
       paths (e.g. `comments[].body`) flatten to bulleted lists. When `text_fields` has
       exactly one bare entry (no `[]`), the label is dropped."""
    if render_template is not None:
        try:
            return render_template.format_map(_SafeDict(rec))
        except Exception:  # noqa: BLE001 — bad template shouldn't crash ingest
            pass  # fall through to labeled rendering

    parts = []
    single_bare = len(text_fields) == 1 and "[" not in text_fields[0]
    for f in text_fields:
        vals = field_values(rec, f)
        if not vals:
            continue
        if len(vals) == 1 and "[" not in f:
            if single_bare:
                parts.append(str(vals[0]))
            else:
                parts.append(f"{f}: {vals[0]}")
        else:
            parts.append(f"{f}:\n- " + "\n- ".join(vals))
    return "\n\n".join(parts)


# Internal knobs for thread-aggregate sub-chunking. Not user-configurable: fixed
# ~200-word chunks at message boundaries + a small overlap match or beat semantic
# chunking for chat data without loading a second embedding model.
_THREAD_MAX_CHARS = 1500  # ~200-400 tokens; under the embedding + Milvus content caps
_THREAD_OVERLAP_MESSAGES = 2  # carry the last N rendered messages into the next sub-chunk


def split_thread(
    rendered: list[str],
    max_chars: int = _THREAD_MAX_CHARS,
    overlap: int = _THREAD_OVERLAP_MESSAGES,
) -> list[tuple[int, int, str]]:
    """Split a thread's rendered messages into size-bounded sub-chunks that break ONLY
    at message boundaries (never mid-message). Adjacent sub-chunks share `overlap`
    trailing messages so cross-chunk references survive. Returns
    [(start_msg_idx, end_msg_idx, text)]. A short thread (joined size <= max_chars)
    returns one item, preserving the single-chunk behaviour."""
    if not rendered:
        return []
    out: list[tuple[int, int, str]] = []
    cur: list[str] = []
    cur_len = 0
    start = 0
    for i, m in enumerate(rendered):
        # +2 accounts for the "\n\n" joiner between messages
        if cur and cur_len + len(m) + 2 > max_chars:
            out.append((start, start + len(cur) - 1, "\n\n".join(cur)))
            carry = cur[-overlap:] if overlap else []
            cur = list(carry)
            cur_len = sum(len(x) + 2 for x in cur)
            start = i - len(carry)
        cur.append(m)
        cur_len += len(m) + 2
    if cur:
        out.append((start, start + len(cur) - 1, "\n\n".join(cur)))
    return out
