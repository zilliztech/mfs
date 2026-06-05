"""End-to-end drain test for the standalone driver: sqlite object_tasks -> producer pool
-> chunks_q -> EmbedConsumer -> (fake) Milvus, with status transitions in sqlite.

No engine.py involvement, no live network: real in-memory sqlite metadata store, fake
plugin, fake Embedder / MilvusSink / TxCacheLike.
"""

from __future__ import annotations

import uuid
from collections import Counter
from unittest.mock import AsyncMock

from mfs_server.config import ServerConfig
from mfs_server.connectors.base import ObjectConfig
from mfs_server.engine.driver import drain_pending
from mfs_server.engine.pipeline import EmbedConsumer, make_chunks_q
from mfs_server.storage.metadata import make_metadata_store

from _fakes import FakeArtifactStore, build_ctx


# --- fake connector plugin ---


class _Ctx:
    def object_config_for(self, path):
        return ObjectConfig()

    def was_partial(self, path):
        return False


class DriverFakePlugin:
    def __init__(self, texts: dict[str, str]):
        self._texts = texts
        self.ctx = _Ctx()

    async def read(self, path, range=None):
        yield self._texts[path].encode()

    def read_records(self, path, range=None):
        return None

    def object_kind_of(self, path):
        return "code" if path.endswith(".py") else "document"


# --- harness ---


async def _make_meta(tmp_path):
    cfg = ServerConfig()
    cfg.metadata.backend = "sqlite"
    cfg.metadata.path = str(tmp_path / "meta.db")
    meta = make_metadata_store(cfg)
    await meta.connect()
    await meta.init_schema()
    await meta.execute("PRAGMA foreign_keys=OFF")  # seed object_tasks without parent rows
    return meta


async def _seed_task(meta, *, job_id, connector_id, object_uri, priority):
    tid = uuid.uuid4().hex
    await meta.execute(
        "INSERT INTO object_tasks (id, connector_job_id, connector_id, object_uri, old_uri, "
        " change_kind, status, priority, attempts) VALUES (?,?,?,?,?,?,?,?,0)",
        (tid, job_id, connector_id, object_uri, None, "added", "pending", priority),
    )
    return tid


def _fake_sink():
    embedder = AsyncMock()
    embedder.batch_embed = AsyncMock(side_effect=lambda texts: [[1.0] for _ in texts])
    milvus = AsyncMock()
    milvus.upsert = AsyncMock()
    milvus.delete_by_object = AsyncMock()
    tx = AsyncMock()
    tx.batch_get = AsyncMock(side_effect=lambda keys: {})
    tx.batch_put = AsyncMock()
    return embedder, milvus, tx


# --- tests ---


async def test_full_drain_single_worker_priority_order(tmp_path):
    meta = await _make_meta(tmp_path)
    # 3 tasks across 2 connectors, mixed priorities (numerically lower = higher priority)
    t_readme = await _seed_task(meta, job_id="jA", connector_id="A", object_uri="/readme.md", priority=-350)
    t_main = await _seed_task(meta, job_id="jA", connector_id="A", object_uri="/main.py", priority=0)
    t_notes = await _seed_task(meta, job_id="jB", connector_id="B", object_uri="/notes.txt", priority=-100)

    plugin_a = DriverFakePlugin({"/readme.md": "# Readme\n\nProject overview.", "/main.py": "def main():\n    return 1\n"})
    plugin_b = DriverFakePlugin({"/notes.txt": "some scratch notes here"})
    resolved = {"A": (plugin_a, "fileA://repo"), "B": (plugin_b, "fileB://repo")}
    claim_order: list[str] = []

    def resolve_plugin(row):
        claim_order.append(row["object_uri"])
        return resolved[row["connector_id"]]

    embedder, milvus, tx = _fake_sink()
    ctx = build_ctx(artifacts=FakeArtifactStore(tmp_path))
    q = make_chunks_q(ServerConfig().embedding.batch_size)
    consumer = EmbedConsumer(embedder, milvus, tx, batch_size=100)

    # extra success hook to prove per-task pending hits zero exactly once each
    succeeded = Counter()
    consumer.register_on_succeeded(lambda uri, job, *a: succeeded.update([uri]))

    await drain_pending(
        meta=meta, ctx=ctx, consumer=consumer, resolve_plugin=resolve_plugin,
        batch_size=100, concurrency=1, chunks_q=q,
    )

    # every task succeeded
    rows = await meta.fetchall("SELECT id, status FROM object_tasks")
    assert {r["id"]: r["status"] for r in rows} == {
        t_readme: "succeeded", t_main: "succeeded", t_notes: "succeeded"
    }

    # higher priority (lower number) ran first: -350, then -100, then 0
    assert claim_order == ["/readme.md", "/notes.txt", "/main.py"]

    # upsert rows carry the right content / uri / kind, routed through chunks_q to the sink
    all_rows = [r for call in milvus.upsert.await_args_list for r in call.args[0]]
    assert all(r["chunk_kind"] == "body" for r in all_rows)
    by_uri = {r["object_uri"]: r["content"] for r in all_rows}
    assert by_uri["fileA://repo/readme.md"].startswith("# Readme")
    assert "def main" in by_uri["fileA://repo/main.py"]
    assert by_uri["fileB://repo/notes.txt"] == "some scratch notes here"

    # delete_by_object exactly once per task (first chunk), not per chunk
    deleted = [call.args for call in milvus.delete_by_object.await_args_list]
    assert Counter(deleted) == Counter({
        ("fileA://repo", "fileA://repo/readme.md"): 1,
        ("fileA://repo", "fileA://repo/main.py"): 1,
        ("fileB://repo", "fileB://repo/notes.txt"): 1,
    })

    # queue fully drained (proves producer -> chunks_q -> consumer, not a direct call)
    assert q.qsize() == 0

    # per-task pending finalized exactly once each
    assert dict(succeeded) == {
        "fileA://repo/readme.md": 1,
        "fileA://repo/main.py": 1,
        "fileB://repo/notes.txt": 1,
    }
    await meta.close()


async def test_full_drain_high_concurrency_all_succeed(tmp_path):
    meta = await _make_meta(tmp_path)
    ids = []
    plugin_texts = {}
    for i in range(12):
        rel = f"/doc{i}.md"
        plugin_texts[rel] = f"# Doc {i}\n\nbody {i}"
        ids.append(await _seed_task(meta, job_id="j", connector_id="C", object_uri=rel, priority=i))
    plugin = DriverFakePlugin(plugin_texts)

    def resolve_plugin(row):
        return (plugin, "file://repo")

    embedder, milvus, tx = _fake_sink()
    ctx = build_ctx(artifacts=FakeArtifactStore(tmp_path))
    consumer = EmbedConsumer(embedder, milvus, tx, batch_size=4)  # multiple flushes

    await drain_pending(
        meta=meta, ctx=ctx, consumer=consumer, resolve_plugin=resolve_plugin,
        batch_size=4, concurrency=8,
    )

    rows = await meta.fetchall("SELECT status FROM object_tasks")
    assert [r["status"] for r in rows] == ["succeeded"] * 12
    # one delete per object, regardless of how flushes batched the chunks
    assert milvus.delete_by_object.await_count == 12
    await meta.close()


async def test_producer_exception_marks_task_failed(tmp_path):
    meta = await _make_meta(tmp_path)
    tid = await _seed_task(meta, job_id="j", connector_id="C", object_uri="/boom.md", priority=0)

    class BoomPlugin(DriverFakePlugin):
        async def read(self, path, range=None):
            raise RuntimeError("read exploded")
            yield b""  # pragma: no cover

    plugin = BoomPlugin({"/boom.md": "x"})

    def resolve_plugin(row):
        return (plugin, "file://repo")

    embedder, milvus, tx = _fake_sink()
    ctx = build_ctx(artifacts=FakeArtifactStore(tmp_path))
    consumer = EmbedConsumer(embedder, milvus, tx, batch_size=10)

    await drain_pending(
        meta=meta, ctx=ctx, consumer=consumer, resolve_plugin=resolve_plugin,
        batch_size=10, concurrency=1,
    )

    row = await meta.fetchone("SELECT status, last_error, attempts FROM object_tasks WHERE id=?", (tid,))
    assert row["status"] == "failed"
    assert "read exploded" in row["last_error"]
    assert row["attempts"] == 1  # incremented once at claim, not again on failure
    milvus.upsert.assert_not_awaited()
    await meta.close()
