"""Metadata DB (design/02 §10.1). SQLite backend (aiosqlite); Postgres backend
added in a later phase (CS mode). Holds connector/object/job state, path index,
fingerprints, file_state, and doubles as the task queue.
"""
from __future__ import annotations

from typing import Any, Optional, Sequence

import aiosqlite

from ..config import ServerConfig

# --- SQLite DDL (design/02 §10.1, incl. this-round objects index-status columns) ---
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
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_jobs_one_queued ON connector_jobs (connector_id) WHERE status = 'queued'",
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

CURRENT_SCHEMA_VERSION = 1


class MetadataStore:
    def __init__(self, cfg: ServerConfig):
        self.backend = cfg.metadata.backend
        self.path = cfg.metadata.path
        self.dsn = cfg.metadata.dsn
        self._db: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        if self.backend != "sqlite":
            raise NotImplementedError(f"metadata backend {self.backend} not yet implemented")
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
        cur = await self._db.execute("SELECT version FROM schema_version WHERE version = ?", (CURRENT_SCHEMA_VERSION,))
        if await cur.fetchone() is None:
            await self._db.execute("INSERT INTO schema_version (version) VALUES (?)", (CURRENT_SCHEMA_VERSION,))
        await self._db.commit()

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        assert self._db is not None
        await self._db.execute(sql, params)
        await self._db.commit()

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
