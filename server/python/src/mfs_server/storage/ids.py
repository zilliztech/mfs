"""Content-addressable IDs: chunk_id (idempotent Milvus primary key) and cache_key.

chunk_id = sha1(namespace_id + connector_uri + object_uri + chunk_kind + locator + lines)
  (design/02 §7 ①). locator/lines disambiguate multiple chunks within one object:
  structured objects use locator (pk/thread_ts/number); body/code/document use lines
  ([start,end] line range). Both can be null for once-per-object kinds (summary etc.).
"""
from __future__ import annotations

import hashlib
import json
from typing import Optional


def sha1_hex(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _canonical(value) -> str:
    if value is None:
        return ""
    # deterministic JSON for dict/list; sort dict keys so {a,b} == {b,a}
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def chunk_id(
    namespace_id: str,
    connector_uri: str,
    object_uri: str,
    chunk_kind: str,
    locator: Optional[dict] = None,
    lines: Optional[list] = None,
) -> str:
    raw = "|".join(
        [
            namespace_id,
            connector_uri,
            object_uri,
            chunk_kind,
            _canonical(locator),
            _canonical(lines),
        ]
    )
    return sha1_hex(raw.encode("utf-8"))


def cache_key(
    input_hash: str,
    kind: str,
    provider: str,
    model: str,
    version: str,
    config: str = "",
) -> str:
    """transformation cache key: sha1(input_hash + kind + provider + model + version + config).

    input_hash is sha1 of the raw input (text/bytes). Single hash so lookup is a plain
    WHERE cache_key IN (...) (design/02 §10.4.1).
    """
    raw = "|".join([input_hash, kind, provider, model, version, config])
    return sha1_hex(raw.encode("utf-8"))
