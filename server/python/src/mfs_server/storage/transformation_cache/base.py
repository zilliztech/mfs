"""Transformation cache: content-addressable memoization of
convert / embedding / vlm / summary results. Logically isolated from metadata DB
(separate SQLite file locally; same Postgres in CS deployments). Best-effort:
losing it only costs recompute, never correctness.

Two subclasses: SqliteTransformationCache (default), PostgresTransformationCache.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS transformation_cache (
        cache_key       TEXT PRIMARY KEY,
        kind            TEXT,
        input_hash      TEXT,
        provider        TEXT,
        model           TEXT,
        model_version   TEXT,
        output_bytes    BLOB,
        output_size     INTEGER,
        hit_count       INTEGER DEFAULT 0,
        created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
        last_hit_at     TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS ix_tx_lru ON transformation_cache (last_hit_at)",
    "CREATE INDEX IF NOT EXISTS ix_tx_kind ON transformation_cache (kind)",
]


PUT_COLS = [
    "cache_key",
    "kind",
    "input_hash",
    "provider",
    "model",
    "model_version",
    "output_bytes",
    "output_size",
]


class TransformationCacheBase(ABC):
    enabled: bool = True
    backend: str = ""

    @property
    def is_pg(self) -> bool:
        return self.backend == "postgres"

    @property
    @abstractmethod
    def _ready(self) -> bool: ...

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def batch_get(self, keys: list[str]) -> dict[str, Optional[bytes]]:
        """Returns {key: output_bytes or None}."""

    @abstractmethod
    async def batch_put(self, entries: list[dict]) -> None:
        """entries: [{cache_key, kind, input_hash, provider, model, model_version,
        output_bytes, output_size}]"""

    @abstractmethod
    async def stats(self) -> dict: ...

    @abstractmethod
    async def close(self) -> None: ...
