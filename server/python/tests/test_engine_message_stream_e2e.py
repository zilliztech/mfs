"""End-to-end: engine routes message_stream ObjectTasks through the NEW pipeline.

A fake slack-like connector exposes one channel object whose read_records yields messages
across two threads (newest-first, as a real message API returns). The engine drains it; the
MessageStreamProducer materializes the records to a per-task raw_records jsonl, regroups by
thread_ts, and emits thread_aggregate chunks. Asserts thread grouping, single per-task
finalize, and that the raw_records artifact is GC'd after the task succeeds (§5.4).
"""

from __future__ import annotations

import asyncio
import uuid
from collections import Counter

from mfs_server.config import ServerConfig
from mfs_server.connectors.base import ObjectConfig, PathStat
from mfs_server.engine.engine import Engine


# --- fakes ---


_SLACK_OCFG = ObjectConfig(
    text_fields=["text"],
    locator_fields=["thread_ts"],
    group_by="thread_ts",
    render_template="{user}: {text}",
)


class _FakeConnCtx:
    def object_config_for(self, path):
        return _SLACK_OCFG

    def was_partial(self, path):
        return False


class _FakeSlackPlugin:
    def __init__(self, records: dict[str, list[dict]]):
        self._records = records
        self.ctx = _FakeConnCtx()
        self.indexed: list[str] = []

    async def connect(self):
        return None

    async def stat(self, rel):
        return PathStat(
            path=rel,
            type="file",
            media_type="application/x-slack-channel",
            size_hint=1,
            fingerprint="fp:" + rel,
        )

    def object_kind_of(self, rel):
        return "message_stream"

    async def read(self, rel, range=None):
        import json

        for r in self._records[rel]:
            yield (json.dumps(r) + "\n").encode()

    def read_records(self, rel, range=None):
        recs = self._records[rel]

        async def gen():
            for r in recs:
                yield r

        return gen()

    async def on_object_indexed(self, rel):
        self.indexed.append(rel)

    async def on_object_deleted(self, rel):
        return None


class _FakeEmbed:
    provider_name = "fake"
    model = "fake-model"
    version = "1"

    def _key(self, text):
        import hashlib

        return "k:" + hashlib.sha1(text.encode()).hexdigest()

    async def _embed_api(self, texts):
        return [[0.1, 0.2] for _ in texts]


class _FakeMilvus:
    def __init__(self):
        self.upserts: list[list[dict]] = []
        self.deletes: list[tuple[str, str]] = []

    def upsert(self, ns, rows):
        self.upserts.append(rows)

    def delete_by_object(self, ns, connector_uri, object_uri):
        self.deletes.append((connector_uri, object_uri))


class _FakeTxCache:
    async def batch_get(self, keys):
        return {k: None for k in keys}

    async def batch_put(self, entries):
        return None


async def _build_engine(tmp_path):
    cfg = ServerConfig()
    cfg.metadata.backend = "sqlite"
    cfg.metadata.path = str(tmp_path / "meta.db")
    cfg.transformation_cache.backend = "sqlite"
    cfg.transformation_cache.db_path = str(tmp_path / "tx.db")
    cfg.artifact_cache.root = str(tmp_path / "art")
    eng = Engine(cfg)
    eng.embed = _FakeEmbed()
    eng.milvus = _FakeMilvus()
    eng.tx_cache = _FakeTxCache()
    eng._embed_idle_ms = 50
    await eng.meta.connect()
    await eng.meta.init_schema()
    await eng.meta.execute("PRAGMA foreign_keys=OFF")
    eng._build_pipeline()
    return eng


async def test_message_stream_routes_through_pipeline(tmp_path):
    eng = await _build_engine(tmp_path)
    # newest-first stream; thread A's reply is separated from its root by thread B.
    records = {
        "/general": [
            {"user": "U2", "text": "reply to A", "thread_ts": "A", "ts": "3"},
            {"user": "U9", "text": "B standalone", "thread_ts": "B", "ts": "2"},
            {"user": "U1", "text": "root of A", "thread_ts": "A", "ts": "1"},
        ]
    }
    plugin = _FakeSlackPlugin(records)
    job_id, cid, connector_uri = "job1", "cA", "slack://acme"
    await eng.meta.execute(
        "INSERT INTO object_tasks (id, connector_job_id, connector_id, object_uri, old_uri, "
        " change_kind, status, priority, attempts) VALUES (?,?,?,?,?,?,?,?,0)",
        (uuid.uuid4().hex, job_id, cid, "/general", None, "added", "pending", 0),
    )

    # extra success hook: prove the channel task finalizes exactly once
    finalized = Counter()
    eng._embed_consumer.register_on_succeeded(lambda uri, j, *a: finalized.update([uri]))

    await asyncio.wait_for(
        eng._run_job_loop(job_id, cid, connector_uri, plugin, threshold=5, consec_fail=0),
        timeout=10,
    )
    await eng._embed_consumer.shutdown()

    rows = [r for batch in eng.milvus.upserts for r in batch]
    # two threads (A, B) -> two thread_aggregate chunks
    assert len(rows) == 2
    assert all(r["chunk_kind"] == "thread_aggregate" for r in rows)
    assert all(r["object_uri"] == "slack://acme/general" for r in rows)
    by_thread = {r["locator"]["thread_ts"]: r["content"] for r in rows}
    assert set(by_thread) == {"A", "B"}
    # thread A aggregates root + reply (in stream order); B is separate
    assert "U2: reply to A" in by_thread["A"] and "U1: root of A" in by_thread["A"]
    assert "B standalone" not in by_thread["A"]
    assert "U9: B standalone" in by_thread["B"]

    # per-object atomic: one delete for the channel object
    assert eng.milvus.deletes == [("slack://acme", "slack://acme/general")]
    # per-task pending hit zero exactly once
    assert dict(finalized) == {"slack://acme/general": 1}

    # task + objects row
    row = await eng.meta.fetchone("SELECT status FROM object_tasks WHERE object_uri='/general'")
    assert row["status"] == "succeeded"
    obj = await eng.meta.fetchone(
        "SELECT search_status, chunk_count FROM objects WHERE object_uri='/general'"
    )
    assert obj["search_status"] == "indexed" and obj["chunk_count"] == 2

    # raw_records jsonl GC'd after the task succeeded (§5.4)
    assert eng.artifact_cache.get_artifact(eng.ns, "slack://acme/general", "raw_records") is None
    await eng.meta.close()


async def test_long_thread_splits_into_subchunks(tmp_path):
    eng = await _build_engine(tmp_path)
    big = "word " * 80  # ~400 chars rendered per message -> exceeds the thread sub-chunk cap
    records = {
        "/general": [
            {"user": f"U{i}", "text": big, "thread_ts": "T", "ts": str(i)} for i in range(10)
        ]
    }
    plugin = _FakeSlackPlugin(records)
    job_id, cid, connector_uri = "job2", "cB", "slack://acme"
    await eng.meta.execute(
        "INSERT INTO object_tasks (id, connector_job_id, connector_id, object_uri, old_uri, "
        " change_kind, status, priority, attempts) VALUES (?,?,?,?,?,?,?,?,0)",
        (uuid.uuid4().hex, job_id, cid, "/general", None, "added", "pending", 0),
    )

    await asyncio.wait_for(
        eng._run_job_loop(job_id, cid, connector_uri, plugin, threshold=5, consec_fail=0),
        timeout=10,
    )
    await eng._embed_consumer.shutdown()

    rows = [r for batch in eng.milvus.upserts for r in batch]
    assert len(rows) > 1  # one long thread split into multiple size-bounded sub-chunks
    for i, r in enumerate(rows):
        assert r["chunk_kind"] == "thread_aggregate"
        assert r["locator"]["thread_ts"] == "T"
        assert r["locator"]["chunk_index"] == i
    row = await eng.meta.fetchone("SELECT status FROM object_tasks WHERE object_uri='/general'")
    assert row["status"] == "succeeded"
    assert eng.artifact_cache.get_artifact(eng.ns, "slack://acme/general", "raw_records") is None
    await eng.meta.close()
