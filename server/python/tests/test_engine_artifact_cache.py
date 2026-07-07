"""ArtifactCacheService unit tests.

Covers the artifact_cache table repository (bytes + metadata row + LRU eviction +
source_key freshness + rename row-rewrite) migrated verbatim from engine.py. Pure
unit tests against an in-memory sqlite metadata store + a real LocalArtifactCache on
a temp dir (no Milvus / embedding / startup). Mirrors the test_engine_object_repo.py
shared-sqlite + shared-art-dir pattern: one db file for the whole run, rows wiped per
test, art dir scrubbed per test so byte-level assertions aren't polluted by residue.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

import pytest

from mfs_server.config import ServerConfig
from mfs_server.connectors.base import PathStat
from mfs_server.engine.engine import Engine

# Shared sqlite db + art dir for the whole run (one meta.db / tx.db / art root), created
# once at import. Each test builds its own Engine/connection into the shared file (so the
# aiosqlite worker thread closes per-test and pytest exits cleanly); rows are wiped and
# the art dir scrubbed in setup so every test starts from a clean schema + clean disk.
_SHARED_DIR = Path(tempfile.mkdtemp(prefix="mfs-engine-artifact-cache"))
_SHARED_META = _SHARED_DIR / "meta.db"
_SHARED_TX = _SHARED_DIR / "tx.db"
_SHARED_ART = _SHARED_DIR / "art"

# Tables a test in this file can seed; wiped in setup so each starts clean against the
# shared db. foreign_keys is OFF, so seeding an objects row without a connector is free.
_RESET_TABLES = ("artifact_cache", "objects", "connectors")

_ENGINES: list[Engine] = []


@pytest.fixture(autouse=True)
async def _reset_and_close():
    """Scrub the art dir + wipe seeded rows before each test (clean disk + clean schema),
    close every Engine built during it afterward (clean process exit)."""
    shutil.rmtree(_SHARED_ART, ignore_errors=True)
    _SHARED_ART.mkdir(parents=True, exist_ok=True)
    yield
    while _ENGINES:
        eng = _ENGINES.pop()
        try:
            await eng.infra.meta.close()
        except Exception:  # noqa: BLE001 — teardown must never mask a test failure
            pass


async def _build_engine(*, max_size_gb: float = 1.0) -> Engine:
    """Build a fresh Engine/connection into the SESSION-SHARED db file + art dir. Schema
    is created idempotently (IF NOT EXISTS), so the first call creates it and later calls
    are no-ops on that front."""
    cfg = ServerConfig()
    cfg.metadata.backend = "sqlite"
    cfg.metadata.path = str(_SHARED_META)
    cfg.transformation_cache.backend = "sqlite"
    cfg.transformation_cache.db_path = str(_SHARED_TX)
    cfg.artifact_cache.root = str(_SHARED_ART)
    cfg.artifact_cache.max_size_gb = max_size_gb
    eng = Engine(cfg)
    await eng.infra.meta.connect()
    await eng.infra.meta.init_schema()
    await eng.infra.meta.execute("PRAGMA foreign_keys=OFF")  # seed rows without parent FKs
    for tbl in _RESET_TABLES:
        await eng.infra.meta.execute(f"DELETE FROM {tbl}")
    _ENGINES.append(eng)
    return eng


def _stat(rel: str, fingerprint: str = "fp") -> PathStat:
    return PathStat(
        path=rel,
        type="file",
        media_type="text/markdown",
        size_hint=10,
        fingerprint=fingerprint,
    )


async def asyncio_sleep(delay: float = 0.01) -> None:
    """isoformat last_accessed has microsecond resolution; a tiny sleep forces two
    _now() calls to produce strictly-ordered strings so recency assertions are meaningful
    rather than coincidentally-equal."""
    await asyncio.sleep(delay)


class _FailingMeta:
    """Wraps a real MetadataStore: ``execute`` / ``executemany`` raise when the SQL contains a
    configured substring, so DB-failure paths (put orphan-bytes cleanup, rename partial-update)
    can be exercised against the real sqlite backend. Reads pass through unchanged so the
    method under test reaches the failing statement rather than failing earlier on a lookup."""

    def __init__(self, real, *, fail_on_substr: str):
        self._real = real
        self._fail = fail_on_substr

    async def execute(self, sql, params=()):
        if self._fail in sql:
            raise RuntimeError(f"simulated DB failure: {self._fail}")
        return await self._real.execute(sql, params)

    async def executemany(self, sql, rows):
        if self._fail in sql:
            raise RuntimeError(f"simulated DB failure: {self._fail}")
        return await self._real.executemany(sql, rows)

    async def fetchone(self, sql, params=()):
        return await self._real.fetchone(sql, params)

    async def fetchall(self, sql, params=()):
        return await self._real.fetchall(sql, params)


class _FlakyStore:
    """Wraps a real LocalArtifactCache: ``delete_artifact`` raises for configured
    ``(object_uri, artifact_kind)`` pairs, so evict's bytes-delete-failure path (keep the row,
    don't decrement total) can be tested. All other ops pass through."""

    def __init__(self, real, *, fail_on: set):
        self._real = real
        self._fail = fail_on  # {(object_uri, artifact_kind), ...}

    def put_artifact(self, *a):
        return self._real.put_artifact(*a)

    def get_artifact(self, *a):
        return self._real.get_artifact(*a)

    def delete_artifact(self, ns, object_uri, kind):
        if (object_uri, kind) in self._fail:
            raise OSError("simulated bytes delete failure")
        self._real.delete_artifact(ns, object_uri, kind)

    def move_artifacts(self, *a):
        return self._real.move_artifacts(*a)

    def artifact_path(self, *a):
        return self._real.artifact_path(*a)


# ----------------------------------------------------------------------
# put_artifact — row write + UPSERT + default currency
# ----------------------------------------------------------------------


async def test_put_artifact_writes_row():
    eng = await _build_engine()
    path = await eng.artifacts.put_artifact(
        eng.ns, "file:///r/a.md", "converted_md", b"hello", "cur1"
    )
    row = await eng.infra.meta.fetchone(
        "SELECT storage_path, source_key, size_bytes, built_at, last_accessed "
        "FROM artifact_cache WHERE namespace_id=? AND object_uri=? AND artifact_kind=?",
        (eng.ns, "file:///r/a.md", "converted_md"),
    )
    assert row is not None
    assert row["storage_path"] == path  # storage_path recorded
    assert row["source_key"] == "cur1"
    assert row["size_bytes"] == 5
    assert row["built_at"]  # non-null timestamps
    assert row["last_accessed"]


async def test_put_artifact_upsert_idempotent():
    eng = await _build_engine()
    for _ in range(2):
        await eng.artifacts.put_artifact(eng.ns, "file:///r/a.md", "converted_md", b"hello", "cur1")
    row = await eng.infra.meta.fetchone(
        "SELECT count(*) AS n FROM artifact_cache "
        "WHERE namespace_id=? AND object_uri=? AND artifact_kind=?",
        (eng.ns, "file:///r/a.md", "converted_md"),
    )
    assert row["n"] == 1  # UPSERT, not a second row


async def test_put_artifact_currency_empty():
    eng = await _build_engine()
    await eng.artifacts.put_artifact(
        eng.ns, "file:///r/a.md", "converted_md", b"hello"
    )  # no currency
    row = await eng.infra.meta.fetchone(
        "SELECT source_key FROM artifact_cache "
        "WHERE namespace_id=? AND object_uri=? AND artifact_kind=?",
        (eng.ns, "file:///r/a.md", "converted_md"),
    )
    assert row["source_key"] == ""  # default currency -> empty source_key


async def test_put_artifact_db_failure_cleans_orphan_bytes():
    """4.1: if the DB upsert fails after bytes were written, compensate by deleting the orphan
    bytes so the cache doesn't accumulate row-less artifacts. The original DB error propagates."""
    eng = await _build_engine()
    eng.artifacts._meta = _FailingMeta(eng.infra.meta, fail_on_substr="INSERT INTO artifact_cache")
    with pytest.raises(RuntimeError, match="simulated DB failure"):
        await eng.artifacts.put_artifact(eng.ns, "file:///r/a.md", "converted_md", b"hello")
    assert (
        eng.infra.artifact_cache.get_artifact(eng.ns, "file:///r/a.md", "converted_md") is None
    )  # bytes cleaned
    assert (
        await eng.infra.meta.fetchone(
            "SELECT 1 FROM artifact_cache WHERE namespace_id=? AND object_uri=?",
            (eng.ns, "file:///r/a.md"),
        )
    ) is None  # row was never written


# ----------------------------------------------------------------------
# read_artifact — hit bumps recency / miss no write
# ----------------------------------------------------------------------


async def test_read_artifact_hit_bumps_last_accessed():
    eng = await _build_engine()
    await eng.artifacts.put_artifact(eng.ns, "file:///r/a.md", "converted_md", b"hello")
    before = await eng.infra.meta.fetchone(
        "SELECT last_accessed FROM artifact_cache "
        "WHERE namespace_id=? AND object_uri=? AND artifact_kind=?",
        (eng.ns, "file:///r/a.md", "converted_md"),
    )
    assert before is not None
    first = before["last_accessed"]

    await asyncio_sleep()  # ensure the recency timestamp strictly advances
    got = await eng.artifacts.read_artifact(eng.ns, "file:///r/a.md", "converted_md")
    assert got == b"hello"
    after = await eng.infra.meta.fetchone(
        "SELECT last_accessed FROM artifact_cache "
        "WHERE namespace_id=? AND object_uri=? AND artifact_kind=?",
        (eng.ns, "file:///r/a.md", "converted_md"),
    )
    assert after["last_accessed"] > first  # recency bumped on read


async def test_read_artifact_miss_no_write():
    eng = await _build_engine()
    got = await eng.artifacts.read_artifact(eng.ns, "file:///r/none.md", "converted_md")
    assert got is None  # miss
    # no row was ever written, so a miss must not create one
    row = await eng.infra.meta.fetchone(
        "SELECT 1 FROM artifact_cache WHERE namespace_id=? AND object_uri=? AND artifact_kind=?",
        (eng.ns, "file:///r/none.md", "converted_md"),
    )
    assert row is None


# ----------------------------------------------------------------------
# read_artifact_fresh — source_key currency check
# ----------------------------------------------------------------------


async def test_read_artifact_fresh_match():
    eng = await _build_engine()
    await eng.artifacts.put_artifact(eng.ns, "file:///r/a.md", "converted_md", b"hello", "cur1")
    got = await eng.artifacts.read_artifact_fresh(eng.ns, "file:///r/a.md", "converted_md", "cur1")
    assert got == b"hello"


async def test_read_artifact_fresh_mismatch():
    eng = await _build_engine()
    await eng.artifacts.put_artifact(eng.ns, "file:///r/a.md", "converted_md", b"hello", "cur1")
    got = await eng.artifacts.read_artifact_fresh(
        eng.ns,
        "file:///r/a.md",
        "converted_md",
        "cur2",  # stale currency
    )
    assert got is None


async def test_read_artifact_fresh_row_absent():
    eng = await _build_engine()
    got = await eng.artifacts.read_artifact_fresh(
        eng.ns, "file:///r/none.md", "converted_md", "cur1"
    )
    assert got is None  # no row -> miss without raising


# ----------------------------------------------------------------------
# converted_md_stale — fingerprint comparison
# ----------------------------------------------------------------------


async def test_converted_md_stale_live_fp_empty():
    eng = await _build_engine()
    # live_fp falsy -> serve the cached copy (can't be cheaply checked)
    assert await eng.artifacts.converted_md_stale("cA", "/a.md", None) is False
    assert await eng.artifacts.converted_md_stale("cA", "/a.md", "") is False


async def test_converted_md_stale_same_fp():
    eng = await _build_engine()
    await eng.objects.write_object_row("cA", "/a.md", _stat("/a.md", "fp1"), True, "indexed", 1)
    assert await eng.artifacts.converted_md_stale("cA", "/a.md", "fp1") is False  # same


async def test_converted_md_stale_diff_fp():
    eng = await _build_engine()
    await eng.objects.write_object_row("cA", "/a.md", _stat("/a.md", "fp1"), True, "indexed", 1)
    assert await eng.artifacts.converted_md_stale("cA", "/a.md", "fp2") is True  # drifted


async def test_converted_md_stale_no_stored():
    eng = await _build_engine()
    # no objects row -> stored is None -> not stale (serve cached / deferred recheck path)
    assert await eng.artifacts.converted_md_stale("cA", "/none.md", "fp1") is False


# ----------------------------------------------------------------------
# drop_artifacts — best-effort purge of all kinds + rows
# ----------------------------------------------------------------------


async def test_drop_artifacts_purges_kinds_and_rows():
    eng = await _build_engine()
    uri = "slack://team/chan"
    for kind in ("converted_md", "vlm_text", "head_cache", "raw_records"):
        await eng.artifacts.put_artifact(eng.ns, uri, kind, b"data-" + kind.encode())

    await eng.artifacts.drop_artifacts(eng.ns, uri)

    rows = await eng.infra.meta.fetchall(
        "SELECT artifact_kind FROM artifact_cache WHERE namespace_id=? AND object_uri=?",
        (eng.ns, uri),
    )
    assert rows == []  # every kind row removed
    # bytes gone too (all four kinds)
    for kind in ("converted_md", "vlm_text", "head_cache", "raw_records"):
        assert eng.infra.artifact_cache.get_artifact(eng.ns, uri, kind) is None


async def test_drop_artifacts_missing_kind_no_raise():
    eng = await _build_engine()
    uri = "slack://team/chan"
    # only one kind exists; the other three are absent — best-effort must not raise
    await eng.artifacts.put_artifact(eng.ns, uri, "converted_md", b"data")

    await eng.artifacts.drop_artifacts(eng.ns, uri)

    rows = await eng.infra.meta.fetchall(
        "SELECT artifact_kind FROM artifact_cache WHERE namespace_id=? AND object_uri=?",
        (eng.ns, uri),
    )
    assert rows == []


async def test_drop_artifacts_purges_unknown_kind():
    """4.2: a kind NOT in _DROP_KINDS must still be purged on drop — the row-enumeration pass
    covers future kinds so introducing a new artifact kind doesn't leak its bytes."""
    eng = await _build_engine()
    uri = "file:///r/x"
    await eng.artifacts.put_artifact(eng.ns, uri, "future_kind", b"future")  # not in _DROP_KINDS
    assert eng.infra.artifact_cache.get_artifact(eng.ns, uri, "future_kind") == b"future"

    await eng.artifacts.drop_artifacts(eng.ns, uri)

    assert eng.infra.artifact_cache.get_artifact(eng.ns, uri, "future_kind") is None  # bytes gone
    assert (
        await eng.infra.meta.fetchone(
            "SELECT 1 FROM artifact_cache WHERE namespace_id=? AND object_uri=?", (eng.ns, uri)
        )
    ) is None  # row gone too


# ----------------------------------------------------------------------
# evict_if_needed — LRU by last_accessed ASC, <= boundary is a no-op
# ----------------------------------------------------------------------


async def test_evict_under_budget_noop():
    eng = await _build_engine(max_size_gb=256 / (1 << 30))  # max_bytes = 256
    await eng.artifacts.put_artifact(eng.ns, "file:///r/a.md", "converted_md", b"x" * 40)
    evicted = await eng.artifacts.evict_if_needed(eng.ns)
    assert evicted == 0  # total (40) < max_bytes (256)
    assert (
        await eng.infra.meta.fetchone(
            "SELECT 1 FROM artifact_cache WHERE namespace_id=? AND object_uri=?",
            (eng.ns, "file:///r/a.md"),
        )
    ) is not None  # row survived


async def test_evict_boundary_equal():
    """total == max_bytes is a no-op (the guard is `total <= max_bytes`)."""
    eng = await _build_engine(max_size_gb=256 / (1 << 30))  # max_bytes = 256
    await eng.artifacts.put_artifact(eng.ns, "file:///r/a.md", "converted_md", b"x" * 256)
    evicted = await eng.artifacts.evict_if_needed(eng.ns)
    assert evicted == 0  # exactly at budget -> not over -> no eviction
    assert (
        await eng.infra.meta.fetchone(
            "SELECT 1 FROM artifact_cache WHERE namespace_id=? AND object_uri=?",
            (eng.ns, "file:///r/a.md"),
        )
    ) is not None


async def test_evict_over_budget_lru():
    """Over budget: evict least-recently-accessed (last_accessed ASC) until total <= max_bytes."""
    eng = await _build_engine(max_size_gb=256 / (1 << 30))  # max_bytes = 256

    # three 128-byte artifacts; a is oldest, c is newest (sleeps force strict recency order)
    await eng.artifacts.put_artifact(eng.ns, "file:///r/a.md", "converted_md", b"x" * 128)
    await asyncio_sleep()
    await eng.artifacts.put_artifact(eng.ns, "file:///r/b.md", "converted_md", b"x" * 128)
    await asyncio_sleep()
    await eng.artifacts.put_artifact(eng.ns, "file:///r/c.md", "converted_md", b"x" * 128)
    # total = 384 > 256; evict ASC -> a first -> 384-128 = 256 <= 256 -> stop

    evicted = await eng.artifacts.evict_if_needed(eng.ns)
    assert evicted == 1

    survivors = {
        r["object_uri"]
        for r in await eng.infra.meta.fetchall(
            "SELECT object_uri FROM artifact_cache WHERE namespace_id=?", (eng.ns,)
        )
    }
    assert survivors == {"file:///r/b.md", "file:///r/c.md"}  # oldest evicted, bytes + row gone
    assert eng.infra.artifact_cache.get_artifact(eng.ns, "file:///r/a.md", "converted_md") is None
    assert (
        eng.infra.artifact_cache.get_artifact(eng.ns, "file:///r/b.md", "converted_md")
        == b"x" * 128
    )


async def test_evict_keeps_row_when_bytes_delete_fails():
    """4.3: when bytes deletion fails, evict leaves the row (retryable on the next sweep) and
    does not subtract from total — no invisible orphan bytes (row deleted but bytes lingering)."""
    eng = await _build_engine(max_size_gb=256 / (1 << 30))  # max_bytes = 256
    await eng.artifacts.put_artifact(eng.ns, "file:///r/a.md", "converted_md", b"x" * 400)
    # total = 400 > 256; single victim a
    eng.artifacts._store = _FlakyStore(
        eng.infra.artifact_cache, fail_on={("file:///r/a.md", "converted_md")}
    )
    evicted = await eng.artifacts.evict_if_needed(eng.ns)
    assert evicted == 0  # a's bytes delete failed -> skipped, nothing evicted
    assert (
        await eng.infra.meta.fetchone(
            "SELECT 1 FROM artifact_cache WHERE namespace_id=? AND object_uri=?",
            (eng.ns, "file:///r/a.md"),
        )
    ) is not None  # row survives (retryable); bytes still on disk (the delete genuinely failed)


# ----------------------------------------------------------------------
# rename_artifacts — move dir + rewrite object_uri/storage_path
# ----------------------------------------------------------------------


async def test_rename_artifacts_updates_rows():
    eng = await _build_engine()
    old_uri = "file:///r/old.md"
    new_uri = "file:///r/new.md"
    await eng.artifacts.put_artifact(eng.ns, old_uri, "converted_md", b"hello", "cur1")

    await eng.artifacts.rename_artifacts(eng.ns, old_uri, new_uri)

    # row rewritten to the new uri + storage_path points at the new uri's path
    row = await eng.infra.meta.fetchone(
        "SELECT object_uri, storage_path, source_key FROM artifact_cache "
        "WHERE namespace_id=? AND object_uri=?",
        (eng.ns, new_uri),
    )
    assert row is not None
    assert row["source_key"] == "cur1"  # source_key preserved
    assert row["storage_path"] == str(
        eng.infra.artifact_cache.artifact_path(eng.ns, new_uri, "converted_md")
    )
    # old uri has no row
    assert (
        await eng.infra.meta.fetchone(
            "SELECT 1 FROM artifact_cache WHERE namespace_id=? AND object_uri=?",
            (eng.ns, old_uri),
        )
    ) is None
    # bytes moved to the new uri (readable via the new uri)
    assert await eng.artifacts.read_artifact(eng.ns, new_uri, "converted_md") == b"hello"


async def test_rename_artifacts_no_rows_leaves_db_unchanged():
    eng = await _build_engine()
    # old_uri has no rows -> the row-rewrite loop doesn't run (no UPDATE issued). The FS move
    # still runs (it may relocate an orphan dir whose row is gone) — that is the pre-service
    # behavior, retained here; "no rows" means "no DB write", not "no side effect".
    await eng.artifacts.rename_artifacts(eng.ns, "file:///r/none.md", "file:///r/new.md")
    assert (
        await eng.infra.meta.fetchone(
            "SELECT 1 FROM artifact_cache WHERE namespace_id=?", (eng.ns,)
        )
    ) is None


async def test_rename_artifacts_multiple_kinds_atomic():
    """4.5: multiple kinds under one object all move to the new uri in a single executemany —
    either every row moves or none do (no partial update)."""
    eng = await _build_engine()
    old_uri = "file:///r/old.md"
    new_uri = "file:///r/new.md"
    await eng.artifacts.put_artifact(eng.ns, old_uri, "converted_md", b"md", "cur1")
    await eng.artifacts.put_artifact(eng.ns, old_uri, "head_cache", b"head", "cur2")

    await eng.artifacts.rename_artifacts(eng.ns, old_uri, new_uri)

    rows = {
        r["artifact_kind"]: r["storage_path"]
        for r in await eng.infra.meta.fetchall(
            "SELECT artifact_kind, storage_path FROM artifact_cache "
            "WHERE namespace_id=? AND object_uri=?",
            (eng.ns, new_uri),
        )
    }
    assert set(rows) == {"converted_md", "head_cache"}  # both moved
    assert rows["converted_md"] == str(
        eng.infra.artifact_cache.artifact_path(eng.ns, new_uri, "converted_md")
    )
    assert rows["head_cache"] == str(
        eng.infra.artifact_cache.artifact_path(eng.ns, new_uri, "head_cache")
    )
    assert (
        await eng.infra.meta.fetchone(
            "SELECT 1 FROM artifact_cache WHERE namespace_id=? AND object_uri=?",
            (eng.ns, old_uri),
        )
    ) is None  # none left under the old uri


async def test_rename_artifacts_db_failure_no_partial_update():
    """4.5: if the row-rewrite UPDATE fails, the single executemany rolls back atomically — no
    row is partially moved. (The pre-service per-row execute loop could leave some rows at the
    new uri and some at the old.) The FS move has already happened, so bytes are at the new uri;
    this test locks only the DB-side no-partial-update guarantee."""
    eng = await _build_engine()
    old_uri = "file:///r/old.md"
    new_uri = "file:///r/new.md"
    await eng.artifacts.put_artifact(eng.ns, old_uri, "converted_md", b"md", "cur1")
    await eng.artifacts.put_artifact(eng.ns, old_uri, "head_cache", b"head", "cur2")
    eng.artifacts._meta = _FailingMeta(
        eng.infra.meta, fail_on_substr="UPDATE artifact_cache SET object_uri"
    )

    with pytest.raises(RuntimeError, match="simulated DB failure"):
        await eng.artifacts.rename_artifacts(eng.ns, old_uri, new_uri)

    # DB failed atomically -> NO row was partially updated; both rows still under old_uri
    rows = await eng.infra.meta.fetchall(
        "SELECT object_uri FROM artifact_cache WHERE namespace_id=?", (eng.ns,)
    )
    assert {r["object_uri"] for r in rows} == {old_uri}


# ----------------------------------------------------------------------
# Engine thin delegates — backward compat for test_artifact_adapter.py / call sites
# ----------------------------------------------------------------------


async def test_engine_delegates_backward_compat():
    """Each Engine._*_artifact / _evict / _converted_md_stale thin delegate behaves
    identically to the ArtifactCacheService method it forwards to — so existing call
    sites (cat / head / _read_full / remove_connector / ArtifactStoreAdapter) and
    test_artifact_adapter.py keep working unchanged."""
    eng = await _build_engine()

    # _put_artifact -> artifacts.put_artifact
    await eng._put_artifact(eng.ns, "file:///r/a.md", "converted_md", b"x", "cur1")
    assert await eng.artifacts.read_artifact(eng.ns, "file:///r/a.md", "converted_md") == b"x"

    # _read_artifact -> artifacts.read_artifact
    assert await eng._read_artifact(eng.ns, "file:///r/a.md", "converted_md") == b"x"

    # _read_artifact_fresh -> artifacts.read_artifact_fresh
    await eng._put_artifact(eng.ns, "file:///r/b.md", "converted_md", b"y", "cur1")
    assert await eng._read_artifact_fresh(eng.ns, "file:///r/b.md", "converted_md", "cur1") == b"y"
    assert await eng._read_artifact_fresh(eng.ns, "file:///r/b.md", "converted_md", "cur2") is None

    # _converted_md_stale -> artifacts.converted_md_stale
    assert await eng._converted_md_stale("cA", "/none.md", None) is False

    # _evict_artifacts_if_needed -> artifacts.evict_if_needed
    assert isinstance(await eng._evict_artifacts_if_needed(eng.ns), int)

    # _drop_artifacts -> artifacts.drop_artifacts
    await eng._drop_artifacts(eng.ns, "file:///r/a.md")
    assert await eng._read_artifact(eng.ns, "file:///r/a.md", "converted_md") is None
