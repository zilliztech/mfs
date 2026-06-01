"""Postgres transformation cache backend."""

from __future__ import annotations

from typing import Optional

from ...config import ServerConfig
from .base import PUT_COLS, SCHEMA, TransformationCacheBase


class PostgresTransformationCache(TransformationCacheBase):
    backend = "postgres"

    def __init__(self, cfg: ServerConfig):
        self.enabled = cfg.transformation_cache.enabled
        self.dsn = cfg.transformation_cache.dsn
        self.lookup_batch_size = cfg.transformation_cache.lookup_batch_size
        self._pool = None

    async def connect(self) -> None:
        if not self.enabled:
            return
        import asyncpg

        self._pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=4)
        async with self._pool.acquire() as c:
            for ddl in SCHEMA:
                await c.execute(
                    ddl.replace("BLOB", "BYTEA").replace(
                        "DEFAULT CURRENT_TIMESTAMP", "DEFAULT now()::text"
                    )
                )

    @property
    def _ready(self) -> bool:
        return self.enabled and self._pool is not None

    async def batch_get(self, keys: list[str]) -> dict[str, Optional[bytes]]:
        result: dict[str, Optional[bytes]] = {k: None for k in keys}
        if not self._ready or not keys:
            return result
        assert self._pool is not None
        async with self._pool.acquire() as c:
            rows = await c.fetch(
                "SELECT cache_key, output_bytes FROM transformation_cache WHERE cache_key = ANY($1)",
                keys,
            )
        for r in rows:
            result[r["cache_key"]] = (
                bytes(r["output_bytes"]) if r["output_bytes"] is not None else None
            )
        return result

    async def batch_put(self, entries: list[dict]) -> None:
        if not self._ready or not entries:
            return
        assert self._pool is not None
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
        args = [tuple(e.get(c) for c in PUT_COLS) for e in entries]
        async with self._pool.acquire() as c:
            await c.executemany(sql, args)

    async def stats(self) -> dict:
        if not self._ready:
            return {"enabled": False}
        assert self._pool is not None
        async with self._pool.acquire() as c:
            row = await c.fetchrow(
                "SELECT count(*) AS n, sum(output_size) AS sz FROM transformation_cache"
            )
        return {"enabled": True, "entry_count": row["n"] or 0, "size_bytes": row["sz"] or 0}

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
