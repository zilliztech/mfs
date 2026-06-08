"""End-to-end: engine routes image ObjectTasks through the NEW pipeline (ImageChunksProducer).

Uses the real CachingVlmClient (with an injected fake LLM provider) + real sqlite
transformation cache so the VLM dedup is exercised for real, plus fake embedder / Milvus.
Asserts vlm_description chunks are upserted, the description_gate caps concurrent VLM
calls (§5.5), identical images dedup via the transformation cache, and that disabling VLM
records the image as metadata-only.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid

from mfs_server.config import ServerConfig
from mfs_server.connectors.base import ObjectConfig, PathStat
from mfs_server.engine.engine import Engine


# --- fakes ---


class _FakeConnCtx:
    def object_config_for(self, path):
        return ObjectConfig()

    def was_partial(self, path):
        return False


class _FakeImagePlugin:
    """Image connector: canned image bytes, image okind."""

    def __init__(self, files: dict[str, bytes]):
        self._files = files
        self.ctx = _FakeConnCtx()
        self.indexed: list[str] = []

    async def connect(self):
        return None

    async def stat(self, rel):
        return PathStat(
            path=rel,
            type="file",
            media_type="image/png",
            size_hint=len(self._files[rel]),
            fingerprint="fp:" + rel,
        )

    def object_kind_of(self, rel):
        return "image"

    async def read(self, rel, range=None):
        yield self._files[rel]

    def read_records(self, rel, range=None):
        return None

    async def on_object_indexed(self, rel):
        self.indexed.append(rel)

    async def on_object_deleted(self, rel):
        return None


class _FakeLLM:
    """VLM provider stand-in: vision() returns a deterministic description per image bytes,
    counts calls, and tracks max in-flight concurrency (to check the description_gate)."""

    def __init__(self, delay: float = 0.05):
        self.calls = 0
        self.delay = delay
        self._inflight = 0
        self.max_inflight = 0

    async def vision(self, prompt, data, mime, *, model, max_tokens):
        self._inflight += 1
        self.max_inflight = max(self.max_inflight, self._inflight)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self._inflight -= 1
        self.calls += 1
        return "desc:" + hashlib.sha1(data).hexdigest()[:8]


class _FakeEmbed:
    provider_name = "fake"
    model = "fake-model"
    version = "1"

    def _key(self, text):
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


async def _build_engine(tmp_path, *, vlm_enabled=True, vlm_concurrency=10, llm_delay=0.05):
    cfg = ServerConfig()
    cfg.metadata.backend = "sqlite"
    cfg.metadata.path = str(tmp_path / "meta.db")
    cfg.transformation_cache.backend = "sqlite"
    cfg.transformation_cache.db_path = str(tmp_path / "tx.db")
    cfg.artifact_cache.root = str(tmp_path / "art")
    cfg.description.enabled = vlm_enabled
    cfg.description.concurrency = (
        vlm_concurrency  # description_gate cap ([description].concurrency)
    )
    eng = Engine(cfg)
    eng.embed = _FakeEmbed()
    eng.milvus = _FakeMilvus()
    eng._embed_idle_ms = 50
    llm = _FakeLLM(delay=llm_delay)
    eng.vlm._llm = llm  # inject fake provider; keep the real CachingVlmClient + real tx_cache dedup
    await eng.meta.connect()
    await eng.meta.init_schema()
    await eng.meta.execute("PRAGMA foreign_keys=OFF")  # seed object_tasks without parent rows
    await eng.tx_cache.connect()
    eng._build_pipeline()
    return eng, llm


async def _seed(eng, *, job_id, cid, files):
    for rel in files:
        await eng.meta.execute(
            "INSERT INTO object_tasks (id, connector_job_id, connector_id, object_uri, old_uri, "
            " change_kind, status, priority, attempts) VALUES (?,?,?,?,?,?,?,?,0)",
            (uuid.uuid4().hex, job_id, cid, rel, None, "added", "pending", 0),
        )


async def test_image_routes_through_pipeline(tmp_path):
    eng, llm = await _build_engine(tmp_path)
    files = {"/a.png": b"\x89PNG-A-bytes", "/b.png": b"\x89PNG-B-bytes"}
    plugin = _FakeImagePlugin(files)
    job_id, cid, connector_uri = "job1", "cA", "file:///repo"
    await _seed(eng, job_id=job_id, cid=cid, files=files)

    await asyncio.wait_for(
        eng._run_job_loop(job_id, cid, connector_uri, plugin, threshold=5, consec_fail=0),
        timeout=10,
    )
    await eng._embed_consumer.shutdown()

    rows = [r for batch in eng.milvus.upserts for r in batch]
    assert len(rows) == 2
    assert all(r["chunk_kind"] == "vlm_description" for r in rows)
    assert all(r["locator"] is None for r in rows)
    assert all(r["content"].startswith("desc:") for r in rows)
    assert {r["object_uri"] for r in rows} == {"file:///repo/a.png", "file:///repo/b.png"}

    # description persisted as a vlm_text artifact (for `mfs cat`)
    art = eng.artifact_cache.get_artifact(eng.ns, "file:///repo/a.png", "vlm_text")
    assert art is not None and art.decode().startswith("desc:")

    # per-object atomic: delete_by_object once per image
    assert sorted(eng.milvus.deletes) == sorted(
        [("file:///repo", "file:///repo/a.png"), ("file:///repo", "file:///repo/b.png")]
    )

    task_rows = await eng.meta.fetchall("SELECT object_uri, status FROM object_tasks")
    assert {r["object_uri"]: r["status"] for r in task_rows} == {
        "/a.png": "succeeded",
        "/b.png": "succeeded",
    }
    obj = await eng.meta.fetchall("SELECT object_uri, search_status, chunk_count FROM objects")
    assert all(o["search_status"] == "indexed" and o["chunk_count"] == 1 for o in obj)
    await eng.meta.close()


async def test_description_gate_caps_concurrent_vlm(tmp_path):
    # gate cap = 2; drive 5 image tasks concurrently (bypassing the sequential job loop) and
    # assert no more than 2 VLM calls are ever in flight at once.
    eng, llm = await _build_engine(tmp_path, vlm_concurrency=2, llm_delay=0.05)
    files = {f"/img{i}.png": f"bytes-{i}".encode() for i in range(5)}
    plugin = _FakeImagePlugin(files)
    connector_uri = "file:///repo"

    async def index(rel):
        task = {"id": uuid.uuid4().hex, "change_kind": "added", "connector_job_id": "j"}
        await eng._index_via_pipeline(
            plugin, connector_uri, rel, connector_uri + rel, "image", task
        )

    await asyncio.wait_for(asyncio.gather(*[index(rel) for rel in files]), timeout=10)
    await eng._embed_consumer.shutdown()

    assert llm.calls == 5  # five distinct images, all hit the provider
    assert llm.max_inflight <= 2  # description_gate held in-flight VLM calls to the cap
    # each image produced one vlm_description chunk, written through chunks_q to the sink
    rows = [r for batch in eng.milvus.upserts for r in batch]
    assert len(rows) == 5 and all(r["chunk_kind"] == "vlm_description" for r in rows)
    await eng.meta.close()


async def test_transformation_cache_dedups_identical_image(tmp_path):
    eng, llm = await _build_engine(tmp_path)
    same = b"\x89PNG-identical-bytes"
    files = {"/x.png": same, "/y.png": same}  # two objects, identical image bytes
    plugin = _FakeImagePlugin(files)
    job_id, cid, connector_uri = "job2", "cB", "file:///repo"
    await _seed(eng, job_id=job_id, cid=cid, files=files)

    await asyncio.wait_for(
        eng._run_job_loop(job_id, cid, connector_uri, plugin, threshold=5, consec_fail=0),
        timeout=10,
    )
    await eng._embed_consumer.shutdown()

    # second identical image is a transformation-cache hit -> the VLM provider runs once
    assert llm.calls == 1
    rows = [r for batch in eng.milvus.upserts for r in batch]
    assert len(rows) == 2  # both objects still get their own vlm_description chunk
    assert {r["content"] for r in rows} == {"desc:" + hashlib.sha1(same).hexdigest()[:8]}
    statuses = await eng.meta.fetchall("SELECT status FROM object_tasks")
    assert [r["status"] for r in statuses] == ["succeeded", "succeeded"]
    await eng.meta.close()


async def test_vlm_disabled_falls_back_to_metadata_only(tmp_path):
    eng, llm = await _build_engine(tmp_path, vlm_enabled=False)
    files = {"/c.png": b"\x89PNG-C"}
    plugin = _FakeImagePlugin(files)
    job_id, cid, connector_uri = "job3", "cC", "file:///repo"
    await _seed(eng, job_id=job_id, cid=cid, files=files)

    await asyncio.wait_for(
        eng._run_job_loop(job_id, cid, connector_uri, plugin, threshold=5, consec_fail=0),
        timeout=10,
    )
    await eng._embed_consumer.shutdown()

    # VLM off: no routing to the pipeline, no VLM call, image recorded as metadata-only
    assert llm.calls == 0
    assert eng.milvus.upserts == []
    row = await eng.meta.fetchone("SELECT status FROM object_tasks WHERE object_uri='/c.png'")
    assert row["status"] == "succeeded"
    obj = await eng.meta.fetchone(
        "SELECT search_status, chunk_count FROM objects WHERE object_uri='/c.png'"
    )
    assert obj["search_status"] == "not_indexed" and obj["chunk_count"] == 0
    await eng.meta.close()
