"""MySQL/MariaDB connector (design/09 Postgres/MySQL) — structured connector,
same table_rows pipeline as postgres. aiomysql; layout /<table>/{schema.json,rows.jsonl}
within the configured database. grep pushdown -> SQL LIKE.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Optional

import aiomysql

from ..base import (
    Capabilities, ConnectorPlugin, Entry, GrepMatch, GrepOptions, HealthStatus,
    ObjectChange, ObjectKind, PathStat, Range, SyncOptions,
)


class MySQLPlugin(ConnectorPlugin):
    NAME = "mysql"
    URI_SCHEME = "mysql"
    DISPLAY_NAME = "MySQL"
    PROMPT = "MySQL tables as /<table>/rows.jsonl + schema.json (within one database)."
    CAPABILITIES = Capabilities(manual_sync=True, watch=False, cursor_kind="updated_at",
                                full_scan=True, delete_detection="full_scan",
                                grep_pushdown=True, search_pushdown=False, paged_cat=True)

    def __init__(self, config, credential, *, ctx):
        super().__init__(config, credential, ctx=ctx)
        self._pool = None

    def _cfg(self, k, d=None):
        return self.config.get(k, d) if isinstance(self.config, dict) else getattr(self.config, k, d)

    async def connect(self) -> None:
        self._pool = await aiomysql.create_pool(
            host=self._cfg("host", "127.0.0.1"), port=int(self._cfg("port", 3306)),
            user=self._cfg("user", "root"), password=str(self._cfg("password", "")),
            db=self._cfg("database"), autocommit=True, minsize=1, maxsize=4)

    async def close(self) -> None:
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None

    async def healthcheck(self) -> HealthStatus:
        try:
            async with self._pool.acquire() as c:
                async with c.cursor() as cur:
                    await cur.execute("SELECT 1")
                    await cur.fetchone()
            return HealthStatus(ok=True)
        except Exception as e:  # noqa: BLE001
            return HealthStatus(ok=False, detail=str(e))

    def _parts(self, path: str) -> list[str]:
        return [p for p in path.strip("/").split("/") if p]

    async def _list_tables(self) -> list[str]:
        async with self._pool.acquire() as c:
            async with c.cursor() as cur:
                await cur.execute("SHOW TABLES")
                return [r[0] for r in await cur.fetchall()]

    def object_kind_of(self, path: str) -> ObjectKind:
        if path.endswith("rows.jsonl"):
            return "table_rows"
        if path.endswith("schema.json"):
            return "table_schema"
        return "directory"

    async def stat(self, path: str) -> PathStat:
        if path.endswith("rows.jsonl"):
            return PathStat(path=path, type="file", media_type="application/x-ndjson",
                            fingerprint=await self.fingerprint(path), extra={"lazy": True})
        if path.endswith("schema.json"):
            return PathStat(path=path, type="file", media_type="application/json")
        return PathStat(path=path, type="dir")

    async def list(self, path: str) -> list[Entry]:
        parts = self._parts(path)
        if len(parts) == 0:
            return [Entry(t, "dir") for t in await self._list_tables()]
        if len(parts) == 1:
            return [Entry("schema.json", "file", "application/json"),
                    Entry("rows.jsonl", "file", "application/x-ndjson", extra={"lazy": True})]
        return []

    async def read_records(self, path: str, range: Optional[Range] = None) -> AsyncIterator[dict]:
        parts = self._parts(path)
        if len(parts) == 2 and parts[1] == "rows.jsonl":
            lim = self._cfg("max_read_rows", 100000)
            async with self._pool.acquire() as c:
                async with c.cursor(aiomysql.DictCursor) as cur:
                    await cur.execute(f"SELECT * FROM `{parts[0]}` LIMIT {lim}")
                    for r in await cur.fetchall():
                        yield r
        elif len(parts) == 2 and parts[1] == "schema.json":
            async with self._pool.acquire() as c:
                async with c.cursor(aiomysql.DictCursor) as cur:
                    await cur.execute(
                        "SELECT column_name, data_type FROM information_schema.columns "
                        "WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",
                        (self._cfg("database"), parts[0]))
                    cols = await cur.fetchall()
            yield {"table": parts[0], "columns": cols}

    async def fingerprint(self, path: str) -> Optional[str]:
        parts = self._parts(path)
        if len(parts) == 2 and parts[1] == "rows.jsonl":
            async with self._pool.acquire() as c:
                async with c.cursor() as cur:
                    await cur.execute(f"SELECT count(*) FROM `{parts[0]}`")
                    cnt = (await cur.fetchone())[0]
            return f"count:{cnt}"
        return None

    async def sync(self, opts: SyncOptions) -> AsyncIterator[ObjectChange]:
        self.ctx.declare_enumeration("full")
        old = await self.state.get("tables") or {}
        seen: dict[str, str] = {}
        for table in await self._list_tables():
            p = f"/{table}/rows.jsonl"
            fp = await self.fingerprint(p) or ""
            seen[p] = fp
            if opts.full or old.get(p) != fp:
                yield ObjectChange(p, "modified" if p in old else "added")
        for p in set(old) - set(seen):
            yield ObjectChange(p, "deleted")
        await self.state.set("tables", seen)

    async def grep(self, pattern: str, path: str, options: GrepOptions) -> Optional[AsyncIterator[GrepMatch]]:
        parts = self._parts(path)
        if len(parts) != 2 or parts[1] != "rows.jsonl" or not options.text_fields:
            return None
        table = parts[0]
        where = " OR ".join(f"`{c}` LIKE %s" for c in options.text_fields)
        args = [f"%{pattern}%"] * len(options.text_fields)
        pool = self._pool

        async def gen():
            async with pool.acquire() as c:
                async with c.cursor(aiomysql.DictCursor) as cur:
                    await cur.execute(f"SELECT * FROM `{table}` WHERE {where} LIMIT 100", args)
                    for r in await cur.fetchall():
                        yield GrepMatch(path=path, content=json.dumps(r, default=str))
        return gen()
