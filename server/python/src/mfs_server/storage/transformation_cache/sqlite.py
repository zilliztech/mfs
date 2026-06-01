"""SQLite transformation cache backend."""

from __future__ import annotations

from typing import Optional

import aiosqlite

from ...config import ServerConfig
from .base import SCHEMA, TransformationCacheBase


class SqliteTransformationCache(TransformationCacheBase):
    backend = "sqlite"

    def __init__(self, cfg: ServerConfig):
        self.enabled = cfg.transformation_cache.enabled
        self.db_path = cfg.transformation_cache.db_path
        self.lookup_batch_size = cfg.transformation_cache.lookup_batch_size
        self._db: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        if not self.enabled:
            return
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        for ddl in SCHEMA:
            await self._db.execute(ddl)
        await self._db.commit()

    @property
    def _ready(self) -> bool:
        return self.enabled and self._db is not None

    async def batch_get(self, keys: list[str]) -> dict[str, Optional[bytes]]:
        result: dict[str, Optional[bytes]] = {k: None for k in keys}
        if not self._ready or not keys:
            return result
        assert self._db is not None
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
        if not self._ready or not entries:
            return
        assert self._db is not None
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
        assert self._db is not None
        cur = await self._db.execute(
            "SELECT count(*) AS n, sum(output_size) AS sz FROM transformation_cache"
        )
        row = await cur.fetchone()
        return {"enabled": True, "entry_count": row["n"] or 0, "size_bytes": row["sz"] or 0}

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None
