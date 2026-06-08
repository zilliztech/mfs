"""End-to-end: a multi-level file tree drains through Map AND the new Reduce subsystem.

root has two sub-dirs (sub1, sub2) each with one .md file. The Map phase indexes the files;
their success notifications drive the bottom-up Reduce subsystem, which emits directory_summary
chunks for sub1, sub2, then root into the same chunks_q. Asserts bottom-up order, that the
chunks reach Milvus, that NO dir_summary object_tasks are created (the Reduce subsystem owns
them, not the Map object_task path), and that the per-job DirTree is evicted on completion.
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


class _FakeFilePlugin:
    def __init__(self, files: dict[str, str]):
        self._files = files
        self.ctx = _FakeConnCtx()
        self.indexed: list[str] = []

    async def connect(self):
        return None

    async def stat(self, rel):
        return PathStat(
            path=rel,
            type="file",
            media_type="text/markdown",
            size_hint=len(self._files[rel]),
            fingerprint="fp:" + rel,
        )

    def object_kind_of(self, rel):
        # .bin files are binary (non-pipeline): they must NOT be folded into the dir tree
        return "binary" if rel.endswith(".bin") else "document"

    async def read(self, rel, range=None):
        yield self._files[rel].encode()

    def read_records(self, rel, range=None):
        return None

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


class _FakeSummaryLLM:
    def __init__(self):
        self.calls = 0

    async def chat(self, prompt, *, model, max_tokens):
        self.calls += 1
        return f"DIRSUMMARY[{self.calls}]"


_FILES = {"/sub1/a.md": "# A\n\nalpha content", "/sub2/b.md": "# B\n\nbeta content"}


async def _build_engine(tmp_path):
    cfg = ServerConfig()
    cfg.metadata.backend = "sqlite"
    cfg.metadata.path = str(tmp_path / "meta.db")
    cfg.transformation_cache.backend = "sqlite"
    cfg.transformation_cache.db_path = str(tmp_path / "tx.db")
    cfg.artifact_cache.root = str(tmp_path / "art")
    cfg.summary.enabled = True  # turns on the Reduce subsystem
    cfg.embedding.batch_size = 1  # flush each chunk immediately so order is deterministic
    eng = Engine(cfg)
    eng.embed = _FakeEmbed()
    eng.milvus = _FakeMilvus()
    eng.tx_cache = _FakeTxCache()
    eng._embed_idle_ms = 50
    await eng.meta.connect()
    await eng.meta.init_schema()
    await eng.meta.execute("PRAGMA foreign_keys=OFF")
    eng._build_pipeline()  # builds + starts the EmbedConsumer AND the Reduce subsystem
    eng.summary._llm = _FakeSummaryLLM()  # inject fake chat provider for directory summaries
    return eng


async def test_dir_summary_reduce_subsystem(tmp_path):
    eng = await _build_engine(tmp_path)
    job_id, cid, connector_uri = "job1", "cA", "file:///r"

    # simulate the sync loop: register the job, feed object changes, finalize the tree
    eng._reduce.register_job(job_id, connector_uri, _FakeFilePlugin(_FILES))
    for rel in _FILES:
        await eng.meta.execute(
            "INSERT INTO object_tasks (id, connector_job_id, connector_id, object_uri, old_uri, "
            " change_kind, status, priority, attempts) VALUES (?,?,?,?,?,?,?,?,0)",
            (uuid.uuid4().hex, job_id, cid, rel, None, "added", "pending", 0),
        )
        eng._reduce.on_yield_object_change(job_id, rel, "document")
    eng._reduce.on_sync_done(job_id)

    # Map phase: index the files. Their success hooks notify the Reduce subsystem.
    plugin = _FakeFilePlugin(_FILES)
    await asyncio.wait_for(
        eng._run_job_loop(job_id, cid, connector_uri, plugin, threshold=5, consec_fail=0),
        timeout=10,
    )
    # block until every directory_summary is computed + persisted
    await asyncio.wait_for(eng._reduce.await_reduce_done(job_id), timeout=10)
    await eng._embed_consumer.shutdown()

    rows = [r for batch in eng.milvus.upserts for r in batch]
    dir_rows = [r for r in rows if r["chunk_kind"] == "directory_summary"]
    # three directory summaries: sub1, sub2, root
    assert {r["object_uri"] for r in dir_rows} == {"file:///r/sub1", "file:///r/sub2", "file:///r/"}
    # bottom-up: the root summary is emitted/persisted AFTER both leaf summaries
    order = [r["object_uri"] for r in dir_rows]
    assert order[-1] == "file:///r/"
    assert set(order[:2]) == {"file:///r/sub1", "file:///r/sub2"}
    # the root summary folded in the leaf summaries (content present on all three)
    assert all(r["content"].startswith("DIRSUMMARY") for r in dir_rows)

    # dir_summary is never an object_task: NO dir_summary object_tasks were ever created
    n = await eng.meta.fetchone(
        "SELECT count(*) AS n FROM object_tasks WHERE change_kind='dir_summary'"
    )
    assert n["n"] == 0

    # per-object atomic for each directory_summary (delete before upsert)
    assert ("file:///r", "file:///r/sub1") in eng.milvus.deletes
    assert ("file:///r", "file:///r/") in eng.milvus.deletes

    # the file Map tasks succeeded
    statuses = await eng.meta.fetchall("SELECT status FROM object_tasks")
    assert [s["status"] for s in statuses] == ["succeeded", "succeeded"]

    # DirTree evicted on job completion (§6.4.6)
    eng._reduce.evict_job(job_id)
    assert job_id not in eng._reduce.builders
    assert job_id not in eng._reduce.queue.job_queues

    await eng._reduce.stop()
    await eng.meta.close()


async def test_binary_file_does_not_wedge_reduce(tmp_path):
    # finding (3): a non-pipeline okind (binary) in a directory takes the inline tail and never
    # fires on_embed_succeeded. If it were counted in the dir tree its parent's pending would
    # stay stuck and reduce would never finish. The engine filters it out via
    # _routes_to_pipeline at the sync call site, so the dir still completes.
    eng = await _build_engine(tmp_path)
    job_id, cid, connector_uri = "jobB", "cB", "file:///r"
    files = {"/sub/a.md": "# A\n\nalpha", "/sub/data.bin": "binary-blob"}

    eng._reduce.register_job(job_id, connector_uri, _FakeFilePlugin(files))
    for rel in files:
        await eng.meta.execute(
            "INSERT INTO object_tasks (id, connector_job_id, connector_id, object_uri, old_uri, "
            " change_kind, status, priority, attempts) VALUES (?,?,?,?,?,?,?,?,0)",
            (uuid.uuid4().hex, job_id, cid, rel, None, "added", "pending", 0),
        )
        # mirror the engine sync loop: only pipeline okinds enter the dir tree
        okind = "binary" if rel.endswith(".bin") else "document"
        if eng._routes_to_pipeline(okind):
            eng._reduce.on_yield_object_change(job_id, rel, okind)
    eng._reduce.on_sync_done(job_id)

    plugin = _FakeFilePlugin(files)
    await asyncio.wait_for(
        eng._run_job_loop(job_id, cid, connector_uri, plugin, threshold=5, consec_fail=0),
        timeout=10,
    )
    # would hang here if the binary's parent pending were stuck
    await asyncio.wait_for(eng._reduce.await_reduce_done(job_id), timeout=10)
    await eng._embed_consumer.shutdown()

    rows = [r for batch in eng.milvus.upserts for r in batch]
    dir_rows = [r for r in rows if r["chunk_kind"] == "directory_summary"]
    assert {r["object_uri"] for r in dir_rows} == {"file:///r/sub", "file:///r/"}
    # the binary was indexed metadata-only (no chunks), the doc produced a body chunk
    statuses = await eng.meta.fetchall("SELECT object_uri, status FROM object_tasks")
    assert {s["object_uri"]: s["status"] for s in statuses} == {
        "/sub/a.md": "succeeded",
        "/sub/data.bin": "succeeded",
    }
    await eng._reduce.stop()
    await eng.meta.close()
