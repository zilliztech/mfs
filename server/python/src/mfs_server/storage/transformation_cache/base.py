"""Transformation cache: content-addressable memoization of
convert / embedding / vlm / summary results. Logically isolated from metadata DB
(separate SQLite file locally; same Postgres in CS deployments). Best-effort:
losing it only costs recompute, never correctness.

Two subclasses: SqliteTransformationCache (default), PostgresTransformationCache.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Awaitable, Callable, Optional

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

    async def get_or_compute(
        self,
        cache_key: str,
        compute_fn: Callable[[], Awaitable[bytes]],
        *,
        kind: str = "",
        input_hash: str = "",
        provider: str = "",
        model: str = "",
        model_version: str = "",
    ) -> bytes:
        """Return the cached bytes for `cache_key`, else compute them via `compute_fn`
        (an async `() -> bytes`) and store them — with a per-key async lock so concurrent
        callers that all miss the same key compute it EXACTLY ONCE (§3.4).

        Why the lock: the Object Lane (ChunksProducer) and the Job Lane
        (SummaryWorker) can both miss the same VLM / summary hash at the same moment; without
        the lock each would fire the (expensive) provider call. With it,
        the first misser computes while the rest await the lock and then pick up its result
        via the double-check. The cache itself stays best-effort: a disabled cache just makes
        every call recompute (batch_get always misses, batch_put no-ops), still serialized
        per key.

        Concrete method shared by both backends; built on the abstract batch_get/batch_put,
        so the stored row carries the same metadata columns as a normal batch_put entry."""
        # 1. fast path: already cached -> no lock needed.
        cached = (await self.batch_get([cache_key])).get(cache_key)
        if cached is not None:
            return cached
        # 2. per-key lock. The event loop is single-threaded, so there is no await between
        #    the hasattr check and setdefault — only the FIRST misser creates the Lock and
        #    every later caller reuses it; no outer mutex is needed. Locks are kept for the
        #    process lifetime (NOT GC'd): evicting a key whose lock is mid-flight would let a
        #    waiter create a second lock and lose synchronization. The set is bounded by the
        #    number of distinct keys; ~100K locks × ~200 B is negligible.
        if not hasattr(self, "_compute_locks"):
            self._compute_locks: dict[str, asyncio.Lock] = {}
        lock = self._compute_locks.setdefault(cache_key, asyncio.Lock())
        async with lock:
            # 3. double-check: a peer may have computed + stored this key while we waited.
            cached = (await self.batch_get([cache_key])).get(cache_key)
            if cached is not None:
                return cached
            # 4. compute (provider call) + store. If compute_fn raises, `async with`
            #    releases the lock and nothing is cached, so the next caller retries cleanly.
            result = await compute_fn()
            await self.batch_put(
                [
                    {
                        "cache_key": cache_key,
                        "kind": kind,
                        "input_hash": input_hash,
                        "provider": provider,
                        "model": model,
                        "model_version": model_version,
                        "output_bytes": result,
                        "output_size": len(result),
                    }
                ]
            )
            return result
