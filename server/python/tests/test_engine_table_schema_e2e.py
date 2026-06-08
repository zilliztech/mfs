"""End-to-end: engine routes table_schema ObjectTasks through the pipeline
(TableSchemaProducer -> schema_summary chunk via the summary gate). Routing is gated on
cfg.summary.enabled.
"""

from __future__ import annotations

import asyncio
import uuid

from mfs_server.config import ServerConfig
from mfs_server.connectors.base import ObjectConfig, PathStat
from mfs_server.engine.engine import Engine


class _FakeConnCtx:
    def object_config_for(self, path):
        return ObjectConfig()

    def was_partial(self, path):
        return False


class _FakeSchemaPlugin:
    def __init__(self, schemas: dict[str, dict]):
        self._schemas = schemas
        self.ctx = _FakeConnCtx()
        self.indexed: list[str] = []

    async def connect(self):
        return None

    async def stat(self, rel):
        return PathStat(
            path=rel,
            type="file",
            media_type="application/x-schema",
            size_hint=1,
            fingerprint="fp:" + rel,
        )

    def object_kind_of(self, rel):
        return "table_schema"

    async def read(self, rel, range=None):
        import json

        yield json.dumps(self._schemas[rel]).encode()

    def read_records(self, rel, range=None):
        schema = self._schemas[rel]

        async def gen():
            yield schema

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


class _FakeLLM:
    """Summary provider stand-in: chat() returns a canned schema description."""

    def __init__(self):
        self.calls = 0

    async def chat(self, prompt, *, model, max_tokens):
        self.calls += 1
        return "This table stores user accounts."


async def _build_engine(tmp_path, *, summary_enabled=True):
    cfg = ServerConfig()
    cfg.metadata.backend = "sqlite"
    cfg.metadata.path = str(tmp_path / "meta.db")
    cfg.transformation_cache.backend = "sqlite"
    cfg.transformation_cache.db_path = str(tmp_path / "tx.db")
    cfg.artifact_cache.root = str(tmp_path / "art")
    cfg.summary.enabled = summary_enabled
    eng = Engine(cfg)
    eng.embed = _FakeEmbed()
    eng.milvus = _FakeMilvus()
    eng.tx_cache = _FakeTxCache()
    eng._embed_idle_ms = 50
    llm = _FakeLLM()
    eng.summary.enabled = summary_enabled
    eng.summary._llm = llm  # inject fake chat provider; keep real CachingSummaryClient + tx_cache
    await eng.meta.connect()
    await eng.meta.init_schema()
    await eng.meta.execute("PRAGMA foreign_keys=OFF")
    eng._build_pipeline()
    return eng, llm


async def _seed(eng, *, job_id, cid, object_uri):
    await eng.meta.execute(
        "INSERT INTO object_tasks (id, connector_job_id, connector_id, object_uri, old_uri, "
        " change_kind, status, priority, attempts) VALUES (?,?,?,?,?,?,?,?,0)",
        (uuid.uuid4().hex, job_id, cid, object_uri, None, "added", "pending", 0),
    )


async def test_table_schema_routes_to_pipeline(tmp_path):
    eng, llm = await _build_engine(tmp_path, summary_enabled=True)
    schemas = {"/public.users": {"table": "users", "columns": [{"name": "id", "type": "int"}]}}
    plugin = _FakeSchemaPlugin(schemas)
    job_id, cid, connector_uri = "job1", "cA", "postgres://db"
    await _seed(eng, job_id=job_id, cid=cid, object_uri="/public.users")

    await asyncio.wait_for(
        eng._run_job_loop(job_id, cid, connector_uri, plugin, threshold=5, consec_fail=0),
        timeout=10,
    )
    await eng._embed_consumer.shutdown()

    rows = [r for batch in eng.milvus.upserts for r in batch]
    assert len(rows) == 1
    assert rows[0]["chunk_kind"] == "schema_summary"
    assert rows[0]["locator"] is None
    assert rows[0]["content"] == "This table stores user accounts."
    assert rows[0]["object_uri"] == "postgres://db/public.users"
    assert llm.calls == 1

    row = await eng.meta.fetchone(
        "SELECT status FROM object_tasks WHERE object_uri='/public.users'"
    )
    assert row["status"] == "succeeded"
    await eng.meta.close()


async def test_table_schema_summary_disabled_metadata_only(tmp_path):
    eng, llm = await _build_engine(tmp_path, summary_enabled=False)
    schemas = {"/public.users": {"table": "users"}}
    plugin = _FakeSchemaPlugin(schemas)
    job_id, cid, connector_uri = "job2", "cB", "postgres://db"
    await _seed(eng, job_id=job_id, cid=cid, object_uri="/public.users")

    await asyncio.wait_for(
        eng._run_job_loop(job_id, cid, connector_uri, plugin, threshold=5, consec_fail=0),
        timeout=10,
    )
    await eng._embed_consumer.shutdown()

    # summary off: no routing, no LLM call, metadata-only
    assert llm.calls == 0
    assert eng.milvus.upserts == []
    obj = await eng.meta.fetchone(
        "SELECT search_status, chunk_count FROM objects WHERE object_uri='/public.users'"
    )
    assert obj["search_status"] == "not_indexed" and obj["chunk_count"] == 0
    row = await eng.meta.fetchone(
        "SELECT status FROM object_tasks WHERE object_uri='/public.users'"
    )
    assert row["status"] == "succeeded"
    await eng.meta.close()
