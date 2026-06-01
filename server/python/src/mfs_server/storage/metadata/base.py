"""Metadata store contract + shared DDL + dialect helpers.

The whole codebase writes SQLite-style `?` placeholders + portable
`ON CONFLICT(...) DO UPDATE SET col=excluded.col`. Backend subclasses translate
to their dialect (Postgres `$n`, BLOB→BYTEA, text default CURRENT_TIMESTAMP), so
call sites stay backend-agnostic.

Bumping CURRENT_SCHEMA_VERSION lets `init_schema()` fail fast (instead of silently
no-op'ing CREATE IF NOT EXISTS) when an existing DB records a different version.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any, Optional, Sequence

# --- shared DDL (SQLite-flavoured; subclasses translate as needed) ----------
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

# Bump on any incompatible metadata DDL change.
CURRENT_SCHEMA_VERSION = 2


# --- dialect helpers (used by Postgres subclass) ----------------------------


def to_pg_ddl(ddl: str) -> str:
    """Adapt the SQLite DDL to Postgres: BLOB->BYTEA; INTEGER (64-bit in SQLite) -> BIGINT
    (PG INTEGER is 32-bit and mtime_ns overflows); text CURRENT_TIMESTAMP default
    (timestamptz can't default a TEXT column) -> now()::text."""
    ddl = re.sub(r"\bBLOB\b", "BYTEA", ddl)
    ddl = re.sub(r"\bINTEGER\b", "BIGINT", ddl)
    ddl = ddl.replace("DEFAULT CURRENT_TIMESTAMP", "DEFAULT now()::text")
    return ddl


def qmark_to_dollar(sql: str) -> str:
    """Translate SQLite `?` placeholders to Postgres `$1, $2, ...` in order. Our SQL
    never contains a literal '?' outside placeholders."""
    n = 0

    def repl(_m):
        nonlocal n
        n += 1
        return f"${n}"

    return re.sub(r"\?", repl, sql)


# --- ABC ----------------------------------------------------------------------


class MetadataStoreBase(ABC):
    """Metadata DB contract. Two subclasses today: SqliteMetadataStore,
    PostgresMetadataStore. Callers write SQLite-style `?` placeholders + portable
    DDL; the subclass adapts."""

    backend: str = ""

    @property
    def is_pg(self) -> bool:
        return self.backend == "postgres"

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def init_schema(self) -> None: ...

    @abstractmethod
    async def execute(self, sql: str, params: Sequence[Any] = ()) -> None: ...

    @abstractmethod
    async def execute_rowcount(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Like execute() but returns the number of affected rows. Enables race-free
        claims across workers: a conditional UPDATE (... WHERE status='queued') wins
        only when it actually flips the row (rowcount == 1). PG row-locks the UPDATE,
        SQLite serializes writers — both make the claim atomic without SKIP LOCKED."""

    @abstractmethod
    async def executemany(self, sql: str, rows: Sequence[Sequence[Any]]) -> None: ...

    @abstractmethod
    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> Optional[dict]: ...

    @abstractmethod
    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[dict]: ...

    @abstractmethod
    async def close(self) -> None: ...

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
