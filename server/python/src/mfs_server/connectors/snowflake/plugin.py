"""Snowflake connector (design/09 BigQuery/Snowflake) — structured, table_rows.
snowflake-connector-python (sync; DictCursor + execute/fetchmany wrapped in
asyncio.to_thread). Layout /<database>/<schema>/tables/<table>/{schema.json,rows.jsonl}.

API verified against snowflake-connector-python docs (connect(...).cursor(DictCursor),
cur.execute(sql), cur.fetchmany(n) -> list[dict], INFORMATION_SCHEMA for catalog).
NOT end-to-end tested (needs Snowflake account).
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Optional

import snowflake.connector
from snowflake.connector import DictCursor

from ..base import (
    Capabilities, ConnectorPlugin, Entry, HealthStatus, ObjectChange, ObjectKind,
    PathStat, Range, SyncOptions, safe_ident,
)


class SnowflakePlugin(ConnectorPlugin):
    NAME = "snowflake"
    URI_SCHEME = "snowflake"
    DISPLAY_NAME = "Snowflake"
    PROMPT = "Snowflake tables as /<database>/<schema>/tables/<table>/rows.jsonl + schema.json."
    CAPABILITIES = Capabilities(manual_sync=True, watch=False, cursor_kind="row_count",
                                full_scan=True, delete_detection="full_scan", paged_cat=True)

    def __init__(self, config, credential, *, ctx):
        super().__init__(config, credential, ctx=ctx)
        self._conn = None

    def _cfg(self, k, d=None):
        return self.config.get(k, d) if isinstance(self.config, dict) else getattr(self.config, k, d)

    async def connect(self) -> None:
        kw = {k: self._cfg(k) for k in ("account", "user", "password", "role",
                                        "warehouse", "database", "schema", "authenticator")
              if self._cfg(k) is not None}
        self._conn = await asyncio.to_thread(snowflake.connector.connect, **kw)

    async def close(self) -> None:
        if self._conn is not None:
            await asyncio.to_thread(self._conn.close)
            self._conn = None

    async def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        def run():
            cur = self._conn.cursor(DictCursor)
            try:
                cur.execute(sql, params)
                return cur.fetchall()
            finally:
                cur.close()
        return await asyncio.to_thread(run)

    async def healthcheck(self) -> HealthStatus:
        try:
            await self._query("SELECT 1")
            return HealthStatus(ok=True)
        except Exception as e:  # noqa: BLE001
            return HealthStatus(ok=False, detail=str(e))

    def _parts(self, path: str) -> list[str]:
        return [p for p in path.strip("/").split("/") if p]

    async def _databases(self) -> list[str]:
        cfg = self._cfg("databases")
        if cfg:
            return list(cfg)
        db = self._cfg("database")
        return [db] if db else []

    async def _schemas(self, database: str) -> list[str]:
        rows = await self._query(
            f'SELECT schema_name FROM "{safe_ident(database)}".information_schema.schemata '
            "WHERE schema_name NOT IN ('INFORMATION_SCHEMA') ORDER BY schema_name")
        return [r["SCHEMA_NAME"] for r in rows]

    async def _tables(self, database: str, schema: str) -> list[str]:
        rows = await self._query(
            f'SELECT table_name FROM "{safe_ident(database)}".information_schema.tables '
            "WHERE table_schema=%s ORDER BY table_name", (schema,))
        return [r["TABLE_NAME"] for r in rows]

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
            return [Entry(d, "dir") for d in await self._databases()]
        if len(parts) == 1:
            return [Entry(s, "dir") for s in await self._schemas(parts[0])]
        if len(parts) == 2:
            return [Entry("tables", "dir")]
        if len(parts) == 3 and parts[2] == "tables":
            return [Entry(t, "dir") for t in await self._tables(parts[0], parts[1])]
        if len(parts) == 4:
            return [Entry("schema.json", "file", "application/json"),
                    Entry("rows.jsonl", "file", "application/x-ndjson", extra={"lazy": True})]
        return []

    async def read_records(self, path: str, range: Optional[Range] = None) -> AsyncIterator[dict]:
        parts = self._parts(path)
        # /<db>/<schema>/tables/<table>/{rows.jsonl,schema.json}
        if len(parts) == 5 and parts[2] == "tables" and parts[4] == "rows.jsonl":
            db, schema, table = safe_ident(parts[0]), safe_ident(parts[1]), safe_ident(parts[3])
            lim = self._cfg("max_read_rows", 100000)
            rows = await self._query(f'SELECT * FROM "{db}"."{schema}"."{table}" LIMIT {lim}')
            for r in rows:
                yield r
        elif len(parts) == 5 and parts[2] == "tables" and parts[4] == "schema.json":
            db, schema, table = safe_ident(parts[0]), safe_ident(parts[1]), safe_ident(parts[3])
            cols = await self._query(
                f'SELECT column_name, data_type FROM "{db}".information_schema.columns '
                "WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position", (schema, table))
            yield {"database": db, "schema": schema, "table": table,
                   "columns": [{"name": c["COLUMN_NAME"], "type": c["DATA_TYPE"]} for c in cols]}

    _CURSOR_CANDIDATES = ("updated_at", "modified_at", "last_modified", "updated", "modified", "mtime")

    async def _cursor_col(self, db: str, schema: str, table: str) -> Optional[str]:
        """The table's change-cursor column (configured `cursor_column` or a common
        timestamp name) so the fingerprint catches in-place updates, not just count."""
        cols = await self._query(
            f'SELECT column_name FROM "{safe_ident(db)}".information_schema.columns '
            "WHERE table_schema=%s AND table_name=%s", (schema, table))
        names = {c["COLUMN_NAME"].lower(): c["COLUMN_NAME"] for c in cols}
        configured = self._cfg("cursor_column")
        if configured and configured.lower() in names:
            return names[configured.lower()]
        for cand in self._CURSOR_CANDIDATES:
            if cand in names:
                return names[cand]
        return None

    async def fingerprint(self, path: str) -> Optional[str]:
        parts = self._parts(path)
        if len(parts) == 5 and parts[2] == "tables" and parts[4] == "rows.jsonl":
            db, schema, table = safe_ident(parts[0]), safe_ident(parts[1]), safe_ident(parts[3])
            cur_col = await self._cursor_col(parts[0], parts[1], parts[3])
            rows = await self._query(f'SELECT count(*) AS n FROM "{db}"."{schema}"."{table}"')
            cnt = rows[0]["N"] if rows else 0
            mx = None
            if cur_col:
                m = await self._query(
                    f'SELECT max("{safe_ident(cur_col)}") AS m FROM "{db}"."{schema}"."{table}"')
                mx = m[0]["M"] if m else None
            return f"count:{cnt}|{cur_col}:{mx}" if cur_col else f"count:{cnt}"
        return None

    async def sync(self, opts: SyncOptions) -> AsyncIterator[ObjectChange]:
        self.ctx.declare_enumeration("full")
        old = await self.state.get("tables") or {}
        seen: dict[str, str] = {}
        for db in await self._databases():
            for schema in await self._schemas(db):
                for table in await self._tables(db, schema):
                    p = f"/{db}/{schema}/tables/{table}/rows.jsonl"
                    fp = await self.fingerprint(p) or ""
                    seen[p] = fp
                    if opts.full or old.get(p) != fp:
                        yield ObjectChange(p, "modified" if p in old else "added")
        for p in set(old) - set(seen):
            yield ObjectChange(p, "deleted")
        await self.state.set("tables", seen)
