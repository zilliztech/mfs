"""SQLite metadata backend — local single-host default."""

from __future__ import annotations

from typing import Any, Optional, Sequence

import aiosqlite

from ...config import ServerConfig
from .base import CURRENT_SCHEMA_VERSION, SQLITE_DDL, MetadataStoreBase


class SqliteMetadataStore(MetadataStoreBase):
    backend = "sqlite"

    def __init__(self, cfg: ServerConfig):
        self.path = cfg.metadata.path
        self._db: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.commit()

    async def init_schema(self) -> None:
        assert self._db is not None
        for ddl in SQLITE_DDL:
            await self._db.execute(ddl)
        cur = await self._db.execute("SELECT version FROM schema_version")
        existing = {r[0] for r in await cur.fetchall()}
        self._guard_schema_version(existing)
        if not existing:
            await self._db.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (CURRENT_SCHEMA_VERSION,)
            )
        await self._db.commit()

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        assert self._db is not None
        await self._db.execute(sql, params)
        await self._db.commit()

    async def execute_rowcount(self, sql: str, params: Sequence[Any] = ()) -> int:
        assert self._db is not None
        cur = await self._db.execute(sql, params)
        await self._db.commit()
        return cur.rowcount

    async def executemany(self, sql: str, rows: Sequence[Sequence[Any]]) -> None:
        assert self._db is not None
        await self._db.executemany(sql, rows)
        await self._db.commit()

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> Optional[dict]:
        assert self._db is not None
        cur = await self._db.execute(sql, params)
        row = await cur.fetchone()
        return dict(row) if row else None

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        assert self._db is not None
        cur = await self._db.execute(sql, params)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None
