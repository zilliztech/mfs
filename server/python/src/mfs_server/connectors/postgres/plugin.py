"""Postgres connector — structured connector template.
asyncpg; virtual layout /<schema>/<table>/{schema.json,rows.jsonl}. read_records
streams rows as dicts; framework's table_rows pipeline does per_row chunk (text_fields
joined) + locator (locator_fields). grep pushdown -> SQL ILIKE.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Optional

import asyncpg

from ..base import (
    Capabilities,
    ConnectorPlugin,
    Entry,
    GrepMatch,
    GrepOptions,
    HealthStatus,
    ObjectChange,
    ObjectKind,
    PathStat,
    Range,
    SyncOptions,
    safe_ident,
)


class PostgresPlugin(ConnectorPlugin):
    NAME = "postgres"
    URI_SCHEME = "postgres"
    DISPLAY_NAME = "Postgres"
    PROMPT = "Postgres tables as /<schema>/<table>/rows.jsonl + schema.json."
    CAPABILITIES = Capabilities(
        manual_sync=True,
        watch=False,
        cursor_kind="updated_at",
        full_scan=True,
        delete_detection="full_scan",
        grep_pushdown=True,
        search_pushdown=False,
        paged_cat=True,
    )

    def __init__(self, config, credential, *, ctx):
        super().__init__(config, credential, ctx=ctx)
        self._pool: Optional[asyncpg.Pool] = None

    def _cfg(self, k, d=None):
        return (
            self.config.get(k, d) if isinstance(self.config, dict) else getattr(self.config, k, d)
        )

    def _dsn(self) -> str:
        return self._cfg("dsn") or self.credential

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(self._dsn(), min_size=1, max_size=4)

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def healthcheck(self) -> HealthStatus:
        try:
            async with self._pool.acquire() as c:
                await c.fetchval("SELECT 1")
            return HealthStatus(ok=True)
        except Exception as e:  # noqa: BLE001
            return HealthStatus(ok=False, detail=str(e))

    def _parts(self, path: str) -> list[str]:
        return [p for p in path.strip("/").split("/") if p]

    async def _list_tables(self, schema: str) -> list[str]:
        async with self._pool.acquire() as c:
            rows = await c.fetch(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema=$1 AND table_type='BASE TABLE' ORDER BY table_name",
                schema,
            )
        return [r["table_name"] for r in rows]

    def object_kind_of(self, path: str) -> ObjectKind:
        if path.endswith("rows.jsonl"):
            return "table_rows"
        if path.endswith("schema.json"):
            return "table_schema"
        return "directory"

    async def stat(self, path: str) -> PathStat:
        if path.endswith("rows.jsonl"):
            return PathStat(
                path=path,
                type="file",
                media_type="application/x-ndjson",
                fingerprint=await self.fingerprint(path),
                extra={"lazy": True},
            )
        if path.endswith("schema.json"):
            return PathStat(path=path, type="file", media_type="application/json")
        return PathStat(path=path, type="dir")

    async def list(self, path: str) -> list[Entry]:
        parts = self._parts(path)
        if len(parts) == 0:
            return [Entry(s, "dir") for s in self._cfg("schemas", ["public"])]
        if len(parts) == 1:
            return [Entry(t, "dir") for t in await self._list_tables(parts[0])]
        if len(parts) == 2:
            return [
                Entry("schema.json", "file", "application/json"),
                Entry("rows.jsonl", "file", "application/x-ndjson", extra={"lazy": True}),
            ]
        return []

    async def _columns(self, schema: str, table: str) -> list[dict]:
        async with self._pool.acquire() as c:
            cols = await c.fetch(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema=$1 AND table_name=$2 ORDER BY ordinal_position",
                schema,
                table,
            )
        return [{"name": r["column_name"], "type": r["data_type"]} for r in cols]

    async def read_records(self, path: str, range: Optional[Range] = None) -> AsyncIterator[dict]:
        parts = self._parts(path)
        if len(parts) == 3 and parts[2] == "schema.json":
            yield {
                "schema": parts[0],
                "table": parts[1],
                "columns": await self._columns(parts[0], parts[1]),
            }
            return
        if len(parts) == 3 and parts[2] == "rows.jsonl":
            schema, table = safe_ident(parts[0]), safe_ident(parts[1])
            lim = self._cfg("max_read_rows", 100000)
            async with self._pool.acquire() as c:
                if range is not None:
                    # cat --range pushdown: page at the source instead of
                    # scanning from the top, so `cat --range 1000000:1000010` is cheap.
                    off = max(0, int(range.start))
                    cnt = max(0, int(range.end) - off)
                    async with c.transaction():
                        async for r in c.cursor(
                            f'SELECT * FROM "{schema}"."{table}" OFFSET {off} LIMIT {cnt}'
                        ):
                            yield dict(r)
                    return
                total = await c.fetchval(f'SELECT count(*) FROM "{schema}"."{table}"')
                if total is not None and total > lim:
                    self.ctx.declare_partial(
                        path
                    )  # capped -> framework marks search_status=partial
                async with c.transaction():  # asyncpg cursors require a transaction
                    async for r in c.cursor(f'SELECT * FROM "{schema}"."{table}" LIMIT {lim}'):
                        yield dict(r)
        elif len(parts) == 3 and parts[2] == "schema.json":
            async with self._pool.acquire() as c:
                cols = await c.fetch(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema=$1 AND table_name=$2 ORDER BY ordinal_position",
                    parts[0],
                    parts[1],
                )
            yield {"schema": parts[0], "table": parts[1], "columns": [dict(x) for x in cols]}

    async def _cursor_col(self, schema: str, table: str) -> Optional[str]:
        """Pick the table's change-cursor column: the configured `cursor_column`, else
        the first of a few common timestamp names actually present. Used to strengthen
        the fingerprint so a row UPDATE (same count) is still detected."""
        async with self._pool.acquire() as c:
            rows = await c.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema=$1 AND table_name=$2",
                schema,
                table,
            )
        names = {r["column_name"].lower(): r["column_name"] for r in rows}
        configured = self._cfg("cursor_column")
        if configured and configured.lower() in names:
            return names[configured.lower()]
        for cand in ("updated_at", "modified_at", "last_modified", "updated", "modified", "mtime"):
            if cand in names:
                return names[cand]
        return None

    async def fingerprint(self, path: str) -> Optional[str]:
        parts = self._parts(path)
        if len(parts) == 3 and parts[2] == "schema.json":
            cols = await self._columns(parts[0], parts[1])
            return "schema:" + ";".join(f"{c['name']}:{c['type']}" for c in cols)
        if len(parts) == 3 and parts[2] == "rows.jsonl":
            schema, table = safe_ident(parts[0]), safe_ident(parts[1])
            cur_col = await self._cursor_col(parts[0], parts[1])
            async with self._pool.acquire() as c:
                cnt = await c.fetchval(f'SELECT count(*) FROM "{schema}"."{table}"')
                mx = (
                    await c.fetchval(
                        f'SELECT max("{safe_ident(cur_col)}") FROM "{schema}"."{table}"'
                    )
                    if cur_col
                    else None
                )
            # count alone misses in-place updates; max(cursor) catches content changes too
            return f"count:{cnt}|{cur_col}:{mx}" if cur_col else f"count:{cnt}"
        return None

    async def sync(self, opts: SyncOptions) -> AsyncIterator[ObjectChange]:
        self.ctx.declare_enumeration("full")
        old = await self.state.get("tables") or {}
        seen: dict[str, str] = {}
        for schema in self._cfg("schemas", ["public"]):
            for table in await self._list_tables(schema):
                for leaf in ("schema.json", "rows.jsonl"):
                    p = f"/{schema}/{table}/{leaf}"
                    fp = await self.fingerprint(p) or ""
                    seen[p] = fp
                    if opts.full or old.get(p) != fp:
                        yield ObjectChange(p, "modified" if p in old else "added")
        for p in set(old) - set(seen):
            yield ObjectChange(p, "deleted")
        await self.state.set("tables", seen)

    async def grep(
        self, pattern: str, path: str, options: GrepOptions
    ) -> Optional[AsyncIterator[GrepMatch]]:
        parts = self._parts(path)
        if len(parts) != 3 or parts[2] != "rows.jsonl" or not options.text_fields:
            return None
        schema, table = safe_ident(parts[0]), safe_ident(parts[1])
        where = " OR ".join(f'"{safe_ident(c)}"::text ILIKE $1' for c in options.text_fields)
        pool = self._pool

        async def gen():
            async with pool.acquire() as c:
                rows = await c.fetch(
                    f'SELECT * FROM "{schema}"."{table}" WHERE {where} LIMIT 100', f"%{pattern}%"
                )
                for r in rows:
                    yield GrepMatch(path=path, content=json.dumps(dict(r), default=str))

        return gen()
