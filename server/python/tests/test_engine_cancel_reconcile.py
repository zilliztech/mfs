"""Regression: a pipeline object whose task is cancelled out from under the shared
EmbedConsumer must not leave orphan chunks in Milvus.

When `mfs job cancel` (or a connector removal racing the shared consumer) flips a task
off 'running' after its chunks were already upserted, the consumer's finalize hook
(`_on_pipeline_object_indexed`) loses the completion claim. It must then delete the
chunks it upserted rather than commit an objects row — otherwise the chunks are
orphaned: un-inspectable and un-cat-able (no objects row points at them), yet still
matchable by search, which queries Milvus directly.
"""

from __future__ import annotations

import uuid

from mfs_server.config import ServerConfig
from mfs_server.connectors.base import PathStat
from mfs_server.engine.engine import Engine


class _RecordingMilvus:
    def __init__(self):
        self.deletes: list[tuple[str, str, str]] = []

    def delete_by_object(self, ns, connector_uri, object_uri):
        self.deletes.append((ns, connector_uri, object_uri))


class _Plugin:
    """Captures cursor advances so the test can assert a cancelled object isn't committed."""

    def __init__(self):
        self.indexed: list[str] = []

    async def on_object_indexed(self, rel):
        self.indexed.append(rel)


async def _build_engine(tmp_path) -> Engine:
    cfg = ServerConfig()
    cfg.metadata.backend = "sqlite"
    cfg.metadata.path = str(tmp_path / "meta.db")
    cfg.transformation_cache.backend = "sqlite"
    cfg.transformation_cache.db_path = str(tmp_path / "tx.db")
    cfg.artifact_cache.root = str(tmp_path / "art")
    eng = Engine(cfg)
    eng.infra.milvus = _RecordingMilvus()
    await eng.infra.meta.connect()
    await eng.infra.meta.init_schema()
    await eng.infra.meta.execute("PRAGMA foreign_keys=OFF")  # seed object_tasks without parent rows
    return eng


def _stat(rel: str) -> PathStat:
    return PathStat(
        path=rel,
        type="file",
        media_type="text/markdown",
        size_hint=10,
        fingerprint="fp:" + rel,
    )


async def _seed_task(eng, *, task_id, job_id, cid, object_uri, status):
    await eng.infra.meta.execute(
        "INSERT INTO object_tasks (id, connector_job_id, connector_id, object_uri, old_uri, "
        " change_kind, status, priority, attempts) VALUES (?,?,?,?,?,?,?,?,0)",
        (task_id, job_id, cid, object_uri, None, "added", status, 0),
    )


async def test_cancelled_pipeline_object_purges_orphan_chunks(tmp_path):
    eng = await _build_engine(tmp_path)
    cid, job_id, connector_uri = "cA", "job1", "file:///repo"
    relpath, task_id = "/a.md", uuid.uuid4().hex
    task_uri = connector_uri + relpath
    # the task was cancelled while its chunks were embedding
    await _seed_task(
        eng, task_id=task_id, job_id=job_id, cid=cid, object_uri=relpath, status="cancelled"
    )
    plugin = _Plugin()
    eng._pending_finalize[task_uri] = (
        cid,
        connector_uri,
        relpath,
        _stat(relpath),
        True,
        plugin,
        task_id,
    )

    # the consumer reports chunks landed, but the completion claim is lost (task != running)
    await eng._on_pipeline_object_indexed(task_uri, job_id, chunk_count=3, partial=False)

    # the chunks it upserted are reconciled away, keyed by the full object uri
    assert eng.infra.milvus.deletes == [(eng.ns, connector_uri, task_uri)]
    # no objects row is committed (nothing for search to resolve) and the cursor isn't advanced
    row = await eng.infra.meta.fetchone(
        "SELECT * FROM objects WHERE connector_id=? AND object_uri=?", (cid, relpath)
    )
    assert row is None
    assert plugin.indexed == []
    await eng.infra.meta.close()


async def test_running_pipeline_object_commits_normally(tmp_path):
    eng = await _build_engine(tmp_path)
    cid, job_id, connector_uri = "cB", "job2", "file:///repo"
    relpath, task_id = "/b.md", uuid.uuid4().hex
    task_uri = connector_uri + relpath
    await _seed_task(
        eng, task_id=task_id, job_id=job_id, cid=cid, object_uri=relpath, status="running"
    )
    plugin = _Plugin()
    eng._pending_finalize[task_uri] = (
        cid,
        connector_uri,
        relpath,
        _stat(relpath),
        True,
        plugin,
        task_id,
    )

    await eng._on_pipeline_object_indexed(task_uri, job_id, chunk_count=3, partial=False)

    # not cancelled: no reconcile delete, objects row committed, cursor advanced, task succeeded
    assert eng.infra.milvus.deletes == []
    row = await eng.infra.meta.fetchone(
        "SELECT search_status, chunk_count FROM objects WHERE connector_id=? AND object_uri=?",
        (cid, relpath),
    )
    assert row["search_status"] == "indexed" and row["chunk_count"] == 3
    assert plugin.indexed == [relpath]
    t = await eng.infra.meta.fetchone("SELECT status FROM object_tasks WHERE id=?", (task_id,))
    assert t["status"] == "succeeded"
    await eng.infra.meta.close()
