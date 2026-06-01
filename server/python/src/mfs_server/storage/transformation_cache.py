"""Transformation cache: content-addressable memoization of
convert / embedding / vlm / summary results. Logically isolated from metadata DB
(separate SQLite file locally; Postgres in CS). Best-effort: losing it only costs
recompute, never correctness.
"""

from __future__ import annotations

from typing import Optional

import aiosqlite

from ..config import ServerConfig

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


_PUT_COLS = [
    "cache_key",
    "kind",
    "input_hash",
    "provider",
    "model",
    "model_version",
    "output_bytes",
    "output_size",
]


class TransformationCache:
    def __init__(self, cfg: ServerConfig):
        self.enabled = cfg.transformation_cache.enabled
        self.backend = cfg.transformation_cache.backend
        self.db_path = cfg.transformation_cache.db_path
        self.dsn = cfg.transformation_cache.dsn
        self.lookup_batch_size = cfg.transformation_cache.lookup_batch_size
        self._db: Optional[aiosqlite.Connection] = None
        self._pool = None  # asyncpg pool (postgres)

    @property
    def is_pg(self) -> bool:
        return self.backend == "postgres"

    async def connect(self) -> None:
        if not self.enabled:
            return
        if self.is_pg:
            import asyncpg

            self._pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=4)
            async with self._pool.acquire() as c:
                for ddl in SCHEMA:
                    await c.execute(
                        ddl.replace("BLOB", "BYTEA").replace(
                            "DEFAULT CURRENT_TIMESTAMP", "DEFAULT now()::text"
                        )
                    )
            return
        if self.backend != "sqlite":
            raise NotImplementedError(f"transformation_cache backend {self.backend} not supported")
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        for ddl in SCHEMA:
            await self._db.execute(ddl)
        await self._db.commit()

    @property
    def _ready(self) -> bool:
        return self.enabled and (self._db is not None or self._pool is not None)

    async def batch_get(self, keys: list[str]) -> dict[str, Optional[bytes]]:
        """Returns {key: output_bytes or None}. Batched by lookup_batch_size."""
        result: dict[str, Optional[bytes]] = {k: None for k in keys}
        if not self._ready or not keys:
            return result
        if self.is_pg:
            async with self._pool.acquire() as c:
                rows = await c.fetch(
                    "SELECT cache_key, output_bytes FROM transformation_cache "
                    "WHERE cache_key = ANY($1)",
                    keys,
                )
            for r in rows:
                result[r["cache_key"]] = (
                    bytes(r["output_bytes"]) if r["output_bytes"] is not None else None
                )
            return result
        for i in range(0, len(keys), self.lookup_batch_size):
            batch = keys[i : i + self.lookup_batch_size]
            ph = ",".join("?" * len(batch))
            cur = await self._db.execute(
                f"SELECT cache_key, output_bytes FROM transformation_cache WHERE cache_key IN ({ph})",
                batch,
            )
            for row in await cur.fetchall():
                result[row["cache_key"]] = row["output_bytes"]
        return result

    async def batch_put(self, entries: list[dict]) -> None:
        """entries: [{cache_key, kind, input_hash, provider, model, model_version,
        output_bytes, output_size}]"""
        if not self._ready or not entries:
            return
        if self.is_pg:
            sql = (
                "INSERT INTO transformation_cache "
                "(cache_key, kind, input_hash, provider, model, model_version, "
                " output_bytes, output_size, last_hit_at) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8, now()::text) "
                "ON CONFLICT (cache_key) DO UPDATE SET "
                " kind=excluded.kind, input_hash=excluded.input_hash, provider=excluded.provider, "
                " model=excluded.model, model_version=excluded.model_version, "
                " output_bytes=excluded.output_bytes, output_size=excluded.output_size, "
                " last_hit_at=now()::text"
            )
            args = [tuple(e.get(c) for c in _PUT_COLS) for e in entries]
            async with self._pool.acquire() as c:
                await c.executemany(sql, args)
            return
        await self._db.executemany(
            "INSERT OR REPLACE INTO transformation_cache "
            "(cache_key, kind, input_hash, provider, model, model_version, "
            " output_bytes, output_size, last_hit_at) "
            "VALUES (:cache_key, :kind, :input_hash, :provider, :model, :model_version, "
            " :output_bytes, :output_size, CURRENT_TIMESTAMP)",
            entries,
        )
        await self._db.commit()

    async def stats(self) -> dict:
        if not self._ready:
            return {"enabled": False}
        if self.is_pg:
            async with self._pool.acquire() as c:
                row = await c.fetchrow(
                    "SELECT count(*) AS n, sum(output_size) AS sz FROM transformation_cache"
                )
            return {"enabled": True, "entry_count": row["n"] or 0, "size_bytes": row["sz"] or 0}
        cur = await self._db.execute(
            "SELECT count(*) AS n, sum(output_size) AS sz FROM transformation_cache"
        )
        row = await cur.fetchone()
        return {"enabled": True, "entry_count": row["n"] or 0, "size_bytes": row["sz"] or 0}

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
        if self._db is not None:
            await self._db.close()
            self._db = None
