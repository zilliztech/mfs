"""Metadata DB. Dual backend: SQLite (aiosqlite, single host) and
Postgres (asyncpg, CS / multi-replica). Holds connector/object/job state, path index,
fingerprints, file_state, and doubles as the task queue.

The whole codebase writes SQLite-style `?` placeholders + portable
`ON CONFLICT(...) DO UPDATE SET col=excluded.col`. For Postgres we translate `?`→`$n`
and adapt the DDL (BLOB→BYTEA, text CURRENT_TIMESTAMP defaults), so call sites are
backend-agnostic.
"""

from __future__ import annotations

import re
from typing import Any, Optional, Sequence

import aiosqlite

from ..config import ServerConfig

# --- SQLite DDL ---
SQLITE_DDL = [
    """
    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER PRIMARY KEY,
        applied_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""",
    """
    CREATE TABLE IF NOT EXISTS connectors (
        id              TEXT PRIMARY KEY,
        namespace_id    TEXT DEFAULT 'default',
        root_uri        TEXT,
        type            TEXT,
        label           TEXT,
        status          TEXT DEFAULT 'active',
        config_json     TEXT,
        config_hash     TEXT,
        credential_ref  TEXT,
        registered_at   TEXT,
        last_health     TEXT,
        health_status   TEXT,
        UNIQUE (namespace_id, root_uri)
    )""",
    """
    CREATE TABLE IF NOT EXISTS objects (
        connector_id    TEXT REFERENCES connectors(id),
        object_uri      TEXT,
        parent_path     TEXT,
        type            TEXT,
        media_type      TEXT,
        size_hint       INTEGER,
        extra_json      TEXT,
        fingerprint     TEXT,
        indexable       INTEGER,
        capabilities    TEXT,
        last_seen       TEXT,
        search_status   TEXT,
        chunk_count     INTEGER,
        index_error     TEXT,
        indexed_at      TEXT,
        PRIMARY KEY (connector_id, object_uri)
    )""",
    "CREATE INDEX IF NOT EXISTS ix_objects_parent ON objects (connector_id, parent_path)",
    """
    CREATE TABLE IF NOT EXISTS artifact_cache (
        namespace_id    TEXT DEFAULT 'default',
        object_uri      TEXT,
        artifact_kind   TEXT,
        storage_path    TEXT,
        fingerprint     TEXT,
        size_bytes      INTEGER,
        built_at        TEXT,
        last_accessed   TEXT,
        PRIMARY KEY (namespace_id, object_uri, artifact_kind)
    )""",
    """
    CREATE TABLE IF NOT EXISTS connector_jobs (
        id                TEXT PRIMARY KEY,
        namespace_id      TEXT DEFAULT 'default',
        connector_id      TEXT REFERENCES connectors(id),
        op_kind           TEXT,
        trigger           TEXT,
        status            TEXT,
        started_at        TEXT,
        finished_at       TEXT,
        heartbeat         TEXT,
        total_objects     INTEGER,
        succeeded_objects INTEGER,
        failed_objects    INTEGER,
        cancelled_objects INTEGER,
        error             TEXT,
        state_snapshot    TEXT
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_jobs_one_running ON connector_jobs (connector_id) WHERE status = 'running'",
    # one in-flight enqueue per connector covers BOTH 'preparing' (job reserved, still
    # enumerating — NOT yet claimable by a worker) and 'queued' (enumeration done, ready).
    # Replaces an older queued-only index; drop it first so existing DBs pick up the change.
    "DROP INDEX IF EXISTS ux_jobs_one_queued",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_jobs_one_pending ON connector_jobs (connector_id) WHERE status IN ('preparing','queued')",
    """
    CREATE TABLE IF NOT EXISTS object_tasks (
        id                TEXT PRIMARY KEY,
        connector_job_id  TEXT REFERENCES connector_jobs(id),
        connector_id      TEXT,
        object_uri        TEXT,
        old_uri           TEXT,
        change_kind       TEXT,
        status            TEXT,
        priority          INTEGER DEFAULT 0,
        attempts          INTEGER DEFAULT 0,
        last_error        TEXT,
        started_at        TEXT,
        finished_at       TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS ix_tasks_sched ON object_tasks (connector_job_id, status, priority)",
    "CREATE INDEX IF NOT EXISTS ix_tasks_running ON object_tasks (status, started_at) WHERE status = 'running'",
    "CREATE INDEX IF NOT EXISTS ix_tasks_connector ON object_tasks (connector_id, status)",
    """
    CREATE TABLE IF NOT EXISTS connector_state (
        connector_id    TEXT,
        key             TEXT,
        value           TEXT,
        updated_at      TEXT,
        PRIMARY KEY (connector_id, key)
    )""",
    """
    CREATE TABLE IF NOT EXISTS watch_grants (
        namespace_id    TEXT DEFAULT 'default',
        connector_id    TEXT REFERENCES connectors(id),
        path            TEXT,
        granted_at      TEXT,
        PRIMARY KEY (namespace_id, path)
    )""",
    """
    CREATE TABLE IF NOT EXISTS file_state (
        namespace_id    TEXT DEFAULT 'default',
        connector_id    TEXT REFERENCES connectors(id),
        path            TEXT,
        size            INTEGER,
        mtime_ns        INTEGER,
        inode           INTEGER,
        sha1            TEXT,
        status          TEXT,
        renamed_from    TEXT,
        staged_at       TEXT,
        indexed_at      TEXT,
        PRIMARY KEY (namespace_id, connector_id, path)
    )""",
    "CREATE INDEX IF NOT EXISTS ix_file_state_staged ON file_state (namespace_id, connector_id, status) WHERE status = 'staged'",
]

# Bump on any incompatible metadata DDL change. init_schema fails fast (rather than
# silently no-op'ing CREATE IF NOT EXISTS) when an existing DB records a different version.
CURRENT_SCHEMA_VERSION = 2


def _to_pg_ddl(ddl: str) -> str:
    """Adapt the SQLite DDL to Postgres: BLOB->BYTEA, text CURRENT_TIMESTAMP default
    (timestamptz can't default a TEXT column) -> now()::text. Everything else
    (TEXT/INTEGER/PRIMARY KEY/UNIQUE/partial indexes/IF NOT EXISTS) is portable."""
    ddl = re.sub(r"\bBLOB\b", "BYTEA", ddl)
    ddl = re.sub(
        r"\bINTEGER\b", "BIGINT", ddl
    )  # SQLite INTEGER is 64-bit; PG INTEGER is 32-bit (mtime_ns overflows)
    ddl = ddl.replace("DEFAULT CURRENT_TIMESTAMP", "DEFAULT now()::text")
    return ddl


def _qmark_to_dollar(sql: str) -> str:
    """Translate SQLite `?` placeholders to Postgres `$1, $2, ...` in order. Our SQL
    never contains a literal '?' outside placeholders."""
    n = 0

    def repl(_m):
        nonlocal n
        n += 1
        return f"${n}"

    return re.sub(r"\?", repl, sql)


class MetadataStore:
    def __init__(self, cfg: ServerConfig):
        self.backend = cfg.metadata.backend
        self.path = cfg.metadata.path
        self.dsn = cfg.metadata.dsn
        self._db: Optional[aiosqlite.Connection] = None
        self._pool = None  # asyncpg pool (postgres)

    @property
    def is_pg(self) -> bool:
        return self.backend == "postgres"

    async def connect(self) -> None:
        if self.is_pg:
            import asyncpg

            self._pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=8)
            return
        if self.backend != "sqlite":
            raise NotImplementedError(f"metadata backend {self.backend} not supported")
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.commit()

    @staticmethod
    def _guard_schema_version(existing: set) -> None:
        """Fail fast when this metadata DB was written by a build with a different schema
        (migrations are out of scope, so we detect rather than silently mismatch): an empty
        set is a fresh DB, exactly {CURRENT} is a match, anything else is incompatible."""
        if existing and existing != {CURRENT_SCHEMA_VERSION}:
            raise RuntimeError(
                f"metadata schema mismatch: DB is version {sorted(existing)}, this build "
                f"expects {CURRENT_SCHEMA_VERSION}. The schema changed across MFS versions "
                f"and migrations are out of scope — point MFS_HOME / the metadata DSN at a "
                f"fresh database (or drop the existing one)."
            )

    async def init_schema(self) -> None:
        if self.is_pg:
            async with self._pool.acquire() as c:
                for ddl in SQLITE_DDL:
                    await c.execute(_to_pg_ddl(ddl))
                rows = await c.fetch("SELECT version FROM schema_version")
                existing = {r["version"] for r in rows}
                self._guard_schema_version(existing)
                if not existing:
                    await c.execute(
                        "INSERT INTO schema_version (version) VALUES ($1)", CURRENT_SCHEMA_VERSION
                    )
            return
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
        if self.is_pg:
            async with self._pool.acquire() as c:
                await c.execute(_qmark_to_dollar(sql), *params)
            return
        assert self._db is not None
        await self._db.execute(sql, params)
        await self._db.commit()

    async def execute_rowcount(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Like execute() but returns the number of affected rows. Enables race-free
        claims across workers: a conditional UPDATE (... WHERE status='queued') wins
        only when it actually flips the row (rowcount == 1). PG row-locks the UPDATE,
        SQLite serializes writers — both make the claim atomic without SKIP LOCKED."""
        if self.is_pg:
            async with self._pool.acquire() as c:
                status = await c.execute(_qmark_to_dollar(sql), *params)
            try:
                return int(status.split()[-1])  # asyncpg command tag, e.g. "UPDATE 1"
            except (ValueError, IndexError, AttributeError):
                return 0
        assert self._db is not None
        cur = await self._db.execute(sql, params)
        await self._db.commit()
        return cur.rowcount

    async def executemany(self, sql: str, rows: Sequence[Sequence[Any]]) -> None:
        if self.is_pg:
            async with self._pool.acquire() as c:
                await c.executemany(_qmark_to_dollar(sql), [tuple(r) for r in rows])
            return
        assert self._db is not None
        await self._db.executemany(sql, rows)
        await self._db.commit()

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> Optional[dict]:
        if self.is_pg:
            async with self._pool.acquire() as c:
                row = await c.fetchrow(_qmark_to_dollar(sql), *params)
            return dict(row) if row else None
        assert self._db is not None
        cur = await self._db.execute(sql, params)
        row = await cur.fetchone()
        return dict(row) if row else None

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        if self.is_pg:
            async with self._pool.acquire() as c:
                rows = await c.fetch(_qmark_to_dollar(sql), *params)
            return [dict(r) for r in rows]
        assert self._db is not None
        cur = await self._db.execute(sql, params)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
        if self._db is not None:
            await self._db.close()
            self._db = None
