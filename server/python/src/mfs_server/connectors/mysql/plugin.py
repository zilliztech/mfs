"""MySQL/MariaDB connector — structured connector,
same table_rows pipeline as postgres. aiomysql; layout /<table>/{schema.json,rows.jsonl}
within the configured database. grep pushdown -> SQL LIKE.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Optional

import aiomysql

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


class MySQLPlugin(ConnectorPlugin):
    NAME = "mysql"
    URI_SCHEME = "mysql"
    DISPLAY_NAME = "MySQL"
    PROMPT = "MySQL tables as /<table>/rows.jsonl + schema.json (within one database)."
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
        self._pool = None

    def _cfg(self, k, d=None):
        return (
            self.config.get(k, d) if isinstance(self.config, dict) else getattr(self.config, k, d)
        )

    async def connect(self) -> None:
        self._pool = await aiomysql.create_pool(
            host=self._cfg("host", "127.0.0.1"),
            port=int(self._cfg("port", 3306)),
            user=self._cfg("user", "root"),
            # fall back to the resolved credential_ref so the password survives reopen
            # (the inline `password` config field is redacted before persistence)
            password=str(self._cfg("password") or self.credential or ""),
            db=self._cfg("database"),
            autocommit=True,
            minsize=1,
            maxsize=4,
        )

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
            return [Entry(t, "dir") for t in await self._list_tables()]
        if len(parts) == 1:
            return [
                Entry("schema.json", "file", "application/json"),
                Entry("rows.jsonl", "file", "application/x-ndjson", extra={"lazy": True}),
            ]
        return []

    async def _columns(self, table: str) -> list[dict]:
        async with self._pool.acquire() as c:
            async with c.cursor() as cur:
                await cur.execute(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",
                    (self._cfg("database"), table),
                )
                rows = await cur.fetchall()
        return [{"name": r[0], "type": r[1]} for r in rows]

    async def read_records(self, path: str, range: Optional[Range] = None) -> AsyncIterator[dict]:
        parts = self._parts(path)
        if len(parts) == 2 and parts[1] == "schema.json":
            yield {"table": parts[0], "columns": await self._columns(parts[0])}
            return
        if len(parts) == 2 and parts[1] == "rows.jsonl":
            lim = self._cfg("max_read_rows", 100000)
            t = safe_ident(parts[0])
            async with self._pool.acquire() as c:
                async with c.cursor(aiomysql.DictCursor) as cur:
                    if range is not None:
                        # cat --range pushdown: page at the source
                        off = max(0, int(range.start))
                        cnt = max(0, int(range.end) - off)
                        await cur.execute(f"SELECT * FROM `{t}` LIMIT %s OFFSET %s", (cnt, off))
                        for r in await cur.fetchall():
                            yield r
                        return
                    await cur.execute(f"SELECT count(*) AS n FROM `{t}`")
                    if (await cur.fetchone())["n"] > lim:
                        self.ctx.declare_partial(path)  # capped -> search_status=partial
                    await cur.execute(f"SELECT * FROM `{t}` LIMIT {lim}")
                    for r in await cur.fetchall():
                        yield r
        elif len(parts) == 2 and parts[1] == "schema.json":
            async with self._pool.acquire() as c:
                async with c.cursor(aiomysql.DictCursor) as cur:
                    await cur.execute(
                        "SELECT column_name, data_type FROM information_schema.columns "
                        "WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",
                        (self._cfg("database"), parts[0]),
                    )
                    cols = await cur.fetchall()
            yield {"table": parts[0], "columns": cols}

    async def _cursor_col(self, table: str) -> Optional[str]:
        """Pick the table's change-cursor column (configured `cursor_column` or a common
        timestamp name present) to strengthen the fingerprint against in-place updates."""
        async with self._pool.acquire() as c:
            async with c.cursor() as cur:
                await cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema=%s AND table_name=%s",
                    (self._cfg("database"), table),
                )
                names = {r[0].lower(): r[0] for r in await cur.fetchall()}
        configured = self._cfg("cursor_column")
        if configured and configured.lower() in names:
            return names[configured.lower()]
        for cand in ("updated_at", "modified_at", "last_modified", "updated", "modified", "mtime"):
            if cand in names:
                return names[cand]
        return None

    async def fingerprint(self, path: str) -> Optional[str]:
        parts = self._parts(path)
        if len(parts) == 2 and parts[1] == "schema.json":
            cols = await self._columns(parts[0])
            return "schema:" + ";".join(f"{c['name']}:{c['type']}" for c in cols)
        if len(parts) == 2 and parts[1] == "rows.jsonl":
            t = safe_ident(parts[0])
            cur_col = await self._cursor_col(parts[0])
            async with self._pool.acquire() as c:
                async with c.cursor() as cur:
                    await cur.execute(f"SELECT count(*) FROM `{t}`")
                    cnt = (await cur.fetchone())[0]
                    mx = None
                    if cur_col:
                        await cur.execute(f"SELECT max(`{safe_ident(cur_col)}`) FROM `{t}`")
                        mx = (await cur.fetchone())[0]
            # count alone misses in-place updates; max(cursor) catches content changes too
            return f"count:{cnt}|{cur_col}:{mx}" if cur_col else f"count:{cnt}"
        return None

    async def sync(self, opts: SyncOptions) -> AsyncIterator[ObjectChange]:
        self.ctx.declare_enumeration("full")
        old = await self.state.get("tables") or {}
        seen: dict[str, str] = {}
        for table in await self._list_tables():
            for leaf in ("schema.json", "rows.jsonl"):
                p = f"/{table}/{leaf}"
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
        if len(parts) != 2 or parts[1] != "rows.jsonl" or not options.text_fields:
            return None
        table = safe_ident(parts[0])
        where = " OR ".join(f"`{safe_ident(c)}` LIKE %s" for c in options.text_fields)
        args = [f"%{pattern}%"] * len(options.text_fields)
        pool = self._pool

        async def gen():
            async with pool.acquire() as c:
                async with c.cursor(aiomysql.DictCursor) as cur:
                    await cur.execute(f"SELECT * FROM `{table}` WHERE {where} LIMIT 100", args)
                    for r in await cur.fetchall():
                        yield GrepMatch(path=path, content=json.dumps(r, default=str))

        return gen()
