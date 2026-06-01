"""Postgres metadata backend — CS / multi-replica deployments."""

from __future__ import annotations

from typing import Any, Optional, Sequence

from ...config import ServerConfig
from .base import CURRENT_SCHEMA_VERSION, SQLITE_DDL, MetadataStoreBase, qmark_to_dollar, to_pg_ddl


class PostgresMetadataStore(MetadataStoreBase):
    backend = "postgres"

    def __init__(self, cfg: ServerConfig):
        self.dsn = cfg.metadata.dsn
        self._pool = None  # asyncpg pool

    async def connect(self) -> None:
        import asyncpg

        self._pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=8)

    async def init_schema(self) -> None:
        assert self._pool is not None
        async with self._pool.acquire() as c:
            for ddl in SQLITE_DDL:
                await c.execute(to_pg_ddl(ddl))
            rows = await c.fetch("SELECT version FROM schema_version")
            existing = {r["version"] for r in rows}
            self._guard_schema_version(existing)
            if not existing:
                await c.execute(
                    "INSERT INTO schema_version (version) VALUES ($1)", CURRENT_SCHEMA_VERSION
                )

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        assert self._pool is not None
        async with self._pool.acquire() as c:
            await c.execute(qmark_to_dollar(sql), *params)

    async def execute_rowcount(self, sql: str, params: Sequence[Any] = ()) -> int:
        assert self._pool is not None
        async with self._pool.acquire() as c:
            status = await c.execute(qmark_to_dollar(sql), *params)
        try:
            return int(status.split()[-1])  # asyncpg command tag, e.g. "UPDATE 1"
        except (ValueError, IndexError, AttributeError):
            return 0

    async def executemany(self, sql: str, rows: Sequence[Sequence[Any]]) -> None:
        assert self._pool is not None
        async with self._pool.acquire() as c:
            await c.executemany(qmark_to_dollar(sql), [tuple(r) for r in rows])

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> Optional[dict]:
        assert self._pool is not None
        async with self._pool.acquire() as c:
            row = await c.fetchrow(qmark_to_dollar(sql), *params)
        return dict(row) if row else None

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        assert self._pool is not None
        async with self._pool.acquire() as c:
            rows = await c.fetch(qmark_to_dollar(sql), *params)
        return [dict(r) for r in rows]

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
