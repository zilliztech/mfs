"""ArtifactCacheService — artifact bytes + metadata row + LRU + freshness.

Consolidates the ``artifact_cache`` table SQL and LRU eviction previously inline in
``engine.py`` (six ``_*_artifact`` / ``_evict`` / ``_converted_md_stale`` methods plus
the rename row-rewrite in ``_index_object``). The storage-layer
``LocalArtifactCache`` (bytes CRUD + path) is injected; this service owns the
metadata-row bookkeeping and the LRU throttle counter. Mirrors the
``ObjectRepository`` pattern (component-local ``_now()``, public methods, SQL migrated
verbatim with zero behavior change). ``Engine`` keeps thin delegate methods so call
sites + ``ArtifactStoreAdapter`` wiring stay unchanged.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Kinds _drop_artifacts best-effort purges on object deletion. converted_md is the
# converter output, head_cache the structured-head snapshot, raw_records the
# message_stream materialization, vlm_text a legacy cleanup target.
_DROP_KINDS = ("converted_md", "vlm_text", "head_cache", "raw_records")


class ArtifactCacheService:
    """SQL repository for the ``artifact_cache`` table: derived bytes (PDF->md, VLM
    image text, head snapshot, message_stream raw_records) per object, with LRU size
    eviction and a ``source_key`` freshness check.

    ``Engine`` delegates every ``self.meta.execute/fetchone/fetchall`` against this
    table to ``self.artifacts.xxx(...)``; SQL is migrated verbatim with zero behavior
    change. The storage-layer ``LocalArtifactCache`` (bytes + paths) stays on
    ``Engine`` (it has non-cache call sites — uploads staging / raw_records GC) and is
    injected here for the bytes CRUD this service needs.
    """

    def __init__(self, cfg, meta, artifact_cache, objects):
        self._cfg = cfg
        self._meta = meta
        self._store = artifact_cache  # LocalArtifactCache (bytes + paths)
        self._obj = objects  # ObjectRepository (converted_md_stale fingerprint)
        self._ns = cfg.namespace
        self._writes = 0  # throttles LRU eviction sweeps (originally Engine._artifact_writes)

    async def put_artifact(
        self, ns: str, object_uri: str, kind: str, data: bytes, currency: str = ""
    ) -> str:
        """Store artifact bytes and record/refresh its artifact_cache row, then run a throttled
        LRU sweep so the cache stays under budget. `source_key` is the caller's currency token
        (source content hash + the producer's self-described identity) — `read_artifact_fresh`
        compares against it so a reuse only hits when source AND producer identity still match;
        kinds that pass no token leave it empty."""
        path = await asyncio.to_thread(self._store.put_artifact, ns, object_uri, kind, data)
        now = _now()
        try:
            await self._meta.execute(
                "INSERT INTO artifact_cache (namespace_id, object_uri, artifact_kind, storage_path, "
                " source_key, size_bytes, built_at, last_accessed) VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(namespace_id, object_uri, artifact_kind) DO UPDATE SET "
                " storage_path=excluded.storage_path, source_key=excluded.source_key, "
                " size_bytes=excluded.size_bytes, built_at=excluded.built_at, "
                " last_accessed=excluded.last_accessed",
                (ns, object_uri, kind, str(path), currency, len(data), now, now),
            )
        except Exception:
            # DB upsert failed: compensate by deleting the orphan bytes we just wrote, so the
            # cache doesn't accumulate row-less artifacts. Best-effort — a cleanup failure is
            # suppressed so the original DB error is what propagates.
            with suppress(Exception):
                await asyncio.to_thread(self._store.delete_artifact, ns, object_uri, kind)
            raise
        self._writes += 1
        if self._writes % 16 == 0:
            await self.evict_if_needed(ns)
        return path

    async def drop_artifacts(self, ns: str, object_uri: str) -> None:
        """Delete all cached artifacts of an object (bytes + artifact_cache rows) — on
        object deletion so the cache doesn't retain orphaned bytes. 'raw_records' is the
        message_stream materialization (jsonl); a deleted Slack/Gmail object would otherwise
        leak it."""
        # Best-effort purge the known kinds — covers orphan bytes whose row is already gone
        # (the row-less bytes _DROP_KINDS was originally written to clean up).
        for kind in _DROP_KINDS:
            try:
                await asyncio.to_thread(self._store.delete_artifact, ns, object_uri, kind)
            except Exception:  # noqa: BLE001
                pass
        # Also purge any kind recorded in the row — covers future kinds not in _DROP_KINDS,
        # so introducing a new artifact kind doesn't leak its bytes on object deletion.
        rows = await self._meta.fetchall(
            "SELECT artifact_kind FROM artifact_cache WHERE namespace_id=? AND object_uri=?",
            (ns, object_uri),
        )
        for r in rows:
            try:
                await asyncio.to_thread(
                    self._store.delete_artifact, ns, object_uri, r["artifact_kind"]
                )
            except Exception:  # noqa: BLE001
                pass
        await self._meta.execute(
            "DELETE FROM artifact_cache WHERE namespace_id=? AND object_uri=?", (ns, object_uri)
        )

    async def read_artifact(self, ns: str, object_uri: str, kind: str) -> bytes | None:
        """Fetch artifact bytes and bump last_accessed (LRU recency) when present."""
        data = await asyncio.to_thread(self._store.get_artifact, ns, object_uri, kind)
        if data is not None:
            await self._meta.execute(
                "UPDATE artifact_cache SET last_accessed=? "
                "WHERE namespace_id=? AND object_uri=? AND artifact_kind=?",
                (_now(), ns, object_uri, kind),
            )
        return data

    async def converted_md_stale(self, cid: str, object_uri: str, live_fp: str | None) -> bool:
        """True when the source's live fingerprint differs from the one recorded at ingest, so
        the cached converted_md no longer reflects the source. `live_fp` comes from the stat()
        the read already did, so this costs one local metadata lookup. A connector that yields
        no fingerprint (live_fp falsy) can't be cheaply checked -> serve the cached copy (the
        deferred snapshot/recheck path for those is TODO §10.9)."""
        if not live_fp:
            return False
        stored = await self._obj.get_object_fingerprint(cid, object_uri)
        return bool(stored) and stored != live_fp

    async def read_artifact_fresh(
        self, ns: str, object_uri: str, kind: str, currency: str
    ) -> bytes | None:
        """Return the artifact bytes only if its stored source_key matches `currency` (same
        source content + producer identity). A mismatch (stale content / upgraded producer)
        returns None so the caller recomputes — this is what lets the Job Lane safely reuse the
        Object Lane's converted_md under parallelism."""
        row = await self._meta.fetchone(
            "SELECT source_key FROM artifact_cache "
            "WHERE namespace_id=? AND object_uri=? AND artifact_kind=?",
            (ns, object_uri, kind),
        )
        if not row or row["source_key"] != currency:
            return None
        return await self.read_artifact(ns, object_uri, kind)

    async def evict_if_needed(self, ns: str) -> int:
        """Evict least-recently-accessed artifacts until total bytes fall under
        artifact_cache.max_size_gb. Returns the number evicted."""
        max_bytes = int(self._cfg.artifact_cache.max_size_gb * (1 << 30))
        row = await self._meta.fetchone(
            "SELECT sum(size_bytes) AS total FROM artifact_cache WHERE namespace_id=?", (ns,)
        )
        total = (row and row["total"]) or 0
        if total <= max_bytes:
            return 0
        victims = await self._meta.fetchall(
            "SELECT object_uri, artifact_kind, size_bytes FROM artifact_cache "
            "WHERE namespace_id=? ORDER BY last_accessed ASC",
            (ns,),
        )
        evicted = 0
        for v in victims:
            if total <= max_bytes:
                break
            try:
                await asyncio.to_thread(
                    self._store.delete_artifact, ns, v["object_uri"], v["artifact_kind"]
                )
            except Exception:  # noqa: BLE001
                # bytes delete failed: leave the row so it stays accounted + retryable on the
                # next sweep, and don't subtract from total (the bytes still occupy space).
                continue
            await self._meta.execute(
                "DELETE FROM artifact_cache WHERE namespace_id=? AND object_uri=? AND artifact_kind=?",
                (ns, v["object_uri"], v["artifact_kind"]),
            )
            total -= v["size_bytes"] or 0
            evicted += 1
        return evicted

    async def rename_artifacts(self, ns: str, old_uri: str, new_uri: str) -> None:
        """Move an object's artifact dir to its new uri and rewrite the artifact_cache rows'
        object_uri + storage_path so LRU bookkeeping (size accounting, last_accessed bumps on
        cat) tracks the artifact under its new uri. ``move_artifacts`` is ``Path.replace``
        (atomic on the FS layer); like the original inline code, no try/except wraps it — an
        FS move failure propagates and the row rewrite is skipped. The per-row UPDATEs are issued
        as a single ``executemany`` so the DB never holds a partially-renamed state (either
        every row moves to the new uri or none do). The FS move is still ahead of the DB write
        and not transactional with it, so a failure between the two can leave FS at the new uri
        + DB at the old — same window as before, but the per-row partial-update case is closed.
        """
        await asyncio.to_thread(self._store.move_artifacts, ns, old_uri, new_uri)
        artifact_rows = await self._meta.fetchall(
            "SELECT artifact_kind FROM artifact_cache WHERE namespace_id=? AND object_uri=?",
            (ns, old_uri),
        )
        if not artifact_rows:
            return  # nothing to rewrite (the FS move above already relocated any orphan dir)
        # compute every row's new storage_path up front, then issue one executemany so the
        # UPDATE is atomic across all rows (single statement + single commit on both backends).
        rows = [
            (
                new_uri,
                str(self._store.artifact_path(ns, new_uri, r["artifact_kind"])),
                ns,
                old_uri,
                r["artifact_kind"],
            )
            for r in artifact_rows
        ]
        await self._meta.executemany(
            "UPDATE artifact_cache SET object_uri=?, storage_path=? "
            "WHERE namespace_id=? AND object_uri=? AND artifact_kind=?",
            rows,
        )
