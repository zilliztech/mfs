"""End-to-end: engine routes record_collection / table_rows ObjectTasks through the NEW
pipeline (RecordCollectionProducer — per-record streaming -> row_text chunks).
"""

from __future__ import annotations

import asyncio
import uuid
from collections import Counter

from mfs_server.config import ServerConfig
from mfs_server.connectors.base import ObjectConfig, PathStat
from mfs_server.engine.engine import Engine


_OCFG = ObjectConfig(
    text_fields=["title", "body"],
    metadata_fields=["state"],
    locator_fields=["number"],
)


class _FakeConnCtx:
    def object_config_for(self, path):
        return _OCFG

    def was_partial(self, path):
        return False


class _FakeRecordPlugin:
    """mongo/issues-like connector: read_records yields dict rows; okind record_collection."""

    def __init__(self, records: dict[str, list[dict]], *, okind="record_collection"):
        self._records = records
        self._okind = okind
        self.ctx = _FakeConnCtx()
        self.indexed: list[str] = []
        self.pulled: list[int] = []  # indices pulled, to observe streaming

    async def connect(self):
        return None

    async def stat(self, rel):
        return PathStat(
            path=rel,
            type="file",
            media_type="application/x-collection",
            size_hint=1,
            fingerprint="fp:" + rel,
        )

    def object_kind_of(self, rel):
        return self._okind

    async def read(self, rel, range=None):
        import json

        for r in self._records[rel]:
            yield (json.dumps(r) + "\n").encode()

    def read_records(self, rel, range=None):
        recs = self._records[rel]
        pulled = self.pulled

        async def gen():
            for i, r in enumerate(recs):
                pulled.append(i)
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


async def _build_engine(tmp_path, *, batch_size=100, idle_ms=50):
    cfg = ServerConfig()
    cfg.metadata.backend = "sqlite"
    cfg.metadata.path = str(tmp_path / "meta.db")
    cfg.transformation_cache.backend = "sqlite"
    cfg.transformation_cache.db_path = str(tmp_path / "tx.db")
    cfg.artifact_cache.root = str(tmp_path / "art")
    cfg.embedding.batch_size = batch_size
    eng = Engine(cfg)
    eng.embed = _FakeEmbed()
    eng.milvus = _FakeMilvus()
    eng.tx_cache = _FakeTxCache()
    eng._embed_idle_ms = idle_ms
    await eng.meta.connect()
    await eng.meta.init_schema()
    await eng.meta.execute("PRAGMA foreign_keys=OFF")
    eng._build_pipeline()
    return eng


async def _seed(eng, *, job_id, cid, object_uri):
    await eng.meta.execute(
        "INSERT INTO object_tasks (id, connector_job_id, connector_id, object_uri, old_uri, "
        " change_kind, status, priority, attempts) VALUES (?,?,?,?,?,?,?,?,0)",
        (uuid.uuid4().hex, job_id, cid, object_uri, None, "added", "pending", 0),
    )


async def test_record_collection_routes_to_pipeline(tmp_path):
    eng = await _build_engine(tmp_path)
    records = {
        "/issues": [
            {"number": 1, "title": "bug", "body": "broken", "state": "open"},
            {"number": 2, "title": "feat", "body": "shiny", "state": "closed"},
        ]
    }
    plugin = _FakeRecordPlugin(records)
    job_id, cid, connector_uri = "job1", "cA", "mongo://db"
    await _seed(eng, job_id=job_id, cid=cid, object_uri="/issues")

    finalized = Counter()
    eng._embed_consumer.register_on_succeeded(lambda uri, j, *a: finalized.update([uri]))

    await asyncio.wait_for(
        eng._run_job_loop(job_id, cid, connector_uri, plugin, threshold=5, consec_fail=0),
        timeout=10,
    )
    await eng._embed_consumer.shutdown()

    rows = [r for batch in eng.milvus.upserts for r in batch]
    assert len(rows) == 2
    assert all(r["chunk_kind"] == "row_text" for r in rows)
    by_loc = {r["locator"]["number"]: r for r in rows}
    assert set(by_loc) == {1, 2}
    assert "title: bug" in by_loc[1]["content"] and "body: broken" in by_loc[1]["content"]
    assert by_loc[1]["metadata"] == {"state": "open"}
    assert all(r["object_uri"] == "mongo://db/issues" for r in rows)

    # per-object atomic + single finalize
    assert eng.milvus.deletes == [("mongo://db", "mongo://db/issues")]
    assert dict(finalized) == {"mongo://db/issues": 1}

    row = await eng.meta.fetchone("SELECT status FROM object_tasks WHERE object_uri='/issues'")
    assert row["status"] == "succeeded"
    await eng.meta.close()


async def test_table_rows_uses_same_record_producer(tmp_path):
    eng = await _build_engine(tmp_path)
    records = {
        "/public.users": [
            {"number": 7, "title": "alice", "body": "admin"},
        ]
    }
    plugin = _FakeRecordPlugin(records, okind="table_rows")  # table_rows -> same producer
    job_id, cid, connector_uri = "job2", "cB", "postgres://db"
    await _seed(eng, job_id=job_id, cid=cid, object_uri="/public.users")

    await asyncio.wait_for(
        eng._run_job_loop(job_id, cid, connector_uri, plugin, threshold=5, consec_fail=0),
        timeout=10,
    )
    await eng._embed_consumer.shutdown()

    rows = [r for batch in eng.milvus.upserts for r in batch]
    assert len(rows) == 1 and rows[0]["chunk_kind"] == "row_text"
    assert rows[0]["locator"]["number"] == 7
    row = await eng.meta.fetchone(
        "SELECT status FROM object_tasks WHERE object_uri='/public.users'"
    )
    assert row["status"] == "succeeded"
    await eng.meta.close()


async def test_streaming_emits_multiple_batches(tmp_path):
    # small embed batch_size + many records -> the pipeline embeds/upserts in several batches
    # as records stream through, rather than buffering the whole collection then one upsert.
    eng = await _build_engine(tmp_path, batch_size=10, idle_ms=50)
    records = {"/issues": [{"number": i, "title": f"t{i}", "body": f"b{i}"} for i in range(25)]}
    plugin = _FakeRecordPlugin(records)
    job_id, cid, connector_uri = "job3", "cC", "mongo://db"
    await _seed(eng, job_id=job_id, cid=cid, object_uri="/issues")

    await asyncio.wait_for(
        eng._run_job_loop(job_id, cid, connector_uri, plugin, threshold=5, consec_fail=0),
        timeout=15,
    )
    await eng._embed_consumer.shutdown()

    rows = [r for batch in eng.milvus.upserts for r in batch]
    assert len(rows) == 25  # every record -> one row_text chunk
    assert len(eng.milvus.upserts) > 1  # multiple flushes => streamed, not one buffered batch
    assert plugin.pulled == list(range(25))  # the record generator was fully consumed
    row = await eng.meta.fetchone("SELECT status FROM object_tasks WHERE object_uri='/issues'")
    assert row["status"] == "succeeded"
    await eng.meta.close()
