"""Proof that _claim_batch pulls pending tasks across the JOBS of one connector (not just the
loop's own job), while staying scoped to that connector. Both jobs here belong to the SAME
connector, so the loop's bound plugin processes every claimed task correctly; tasks of a
different connector are NOT claimed (they need that connector's plugin — a cross-connector
worker pool with per-task plugin resolution is a separate change).
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


class _FakeDocPlugin:
    def __init__(self, texts: dict[str, str]):
        self._texts = texts
        self.ctx = _FakeConnCtx()
        self.processed: list[str] = []  # object_uris in the order they were read/processed

    async def connect(self):
        return None

    async def stat(self, rel):
        return PathStat(
            path=rel,
            type="file",
            media_type="text/markdown",
            size_hint=len(self._texts[rel]),
            fingerprint="fp:" + rel,
        )

    def object_kind_of(self, rel):
        return "document"

    async def read(self, rel, range=None):
        self.processed.append(rel)
        yield self._texts[rel].encode()

    def read_records(self, rel, range=None):
        return None

    async def on_object_indexed(self, rel):
        return None

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
    def upsert(self, ns, rows):
        return None

    def delete_by_object(self, ns, connector_uri, object_uri):
        return None


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
    eng._embed_idle_ms = 30
    await eng.meta.connect()
    await eng.meta.init_schema()
    await eng.meta.execute("PRAGMA foreign_keys=OFF")
    eng._build_pipeline()
    return eng


async def _seed(eng, *, job_id, cid, object_uri, started_at):
    # explicit started_at so the global ORDER BY priority, started_at is deterministic
    await eng.meta.execute(
        "INSERT INTO object_tasks (id, connector_job_id, connector_id, object_uri, old_uri, "
        " change_kind, status, priority, attempts, started_at) VALUES (?,?,?,?,?,?,?,?,0,?)",
        (uuid.uuid4().hex, job_id, cid, object_uri, None, "added", "pending", 0, started_at),
    )


async def test_single_loop_drains_other_jobs_tasks(tmp_path):
    eng = await _build_engine(tmp_path)
    cid, connector_uri = "cShared", "file:///repo"
    texts = {f"/a{i}.md": f"# A{i}\n\nbody" for i in (1, 2)}
    texts.update({f"/b{i}.md": f"# B{i}\n\nbody" for i in (1, 2)})
    plugin = _FakeDocPlugin(texts)
    # two DIFFERENT jobs on the same connector, same priority, interleaved start times
    await _seed(eng, job_id="jobA", cid=cid, object_uri="/a1.md", started_at="2026-01-01T00:00:01")
    await _seed(eng, job_id="jobB", cid=cid, object_uri="/b1.md", started_at="2026-01-01T00:00:02")
    await _seed(eng, job_id="jobA", cid=cid, object_uri="/a2.md", started_at="2026-01-01T00:00:03")
    await _seed(eng, job_id="jobB", cid=cid, object_uri="/b2.md", started_at="2026-01-01T00:00:04")

    # run ONE loop whose owning job is jobA; with the per-job filter gone it ALSO drains jobB.
    await asyncio.wait_for(
        eng._run_job_loop("jobA", cid, connector_uri, plugin, threshold=5, consec_fail=0),
        timeout=10,
    )
    await eng._embed_consumer.shutdown()

    # global proof: jobB's tasks were drained by the jobA-owned loop
    rows = await eng.meta.fetchall("SELECT object_uri, connector_job_id, status FROM object_tasks")
    assert {r["object_uri"]: r["status"] for r in rows} == {
        "/a1.md": "succeeded",
        "/b1.md": "succeeded",
        "/a2.md": "succeeded",
        "/b2.md": "succeeded",
    }
    assert {r["connector_job_id"] for r in rows} == {"jobA", "jobB"}
    # deterministic global order (priority, started_at) -> interleaved across the two jobs
    assert plugin.processed == ["/a1.md", "/b1.md", "/a2.md", "/b2.md"]
    await eng.meta.close()


async def test_claim_scoped_to_connector(tmp_path):
    # A connector's loop must NOT claim another connector's tasks: it would read them with the
    # wrong plugin. The other connector's task stays pending for its own drain.
    eng = await _build_engine(tmp_path)
    texts = {"/a.md": "# A\n\nbody", "/b.md": "# B\n\nbody"}
    plugin = _FakeDocPlugin(texts)
    await _seed(eng, job_id="job1", cid="c1", object_uri="/a.md", started_at="2026-01-01T00:00:01")
    await _seed(eng, job_id="job2", cid="c2", object_uri="/b.md", started_at="2026-01-01T00:00:02")

    # loop owns connector c1 only
    await asyncio.wait_for(
        eng._run_job_loop("job1", "c1", "file:///repo1", plugin, threshold=5, consec_fail=0),
        timeout=10,
    )
    await eng._embed_consumer.shutdown()

    rows = await eng.meta.fetchall("SELECT object_uri, status FROM object_tasks")
    statuses = {r["object_uri"]: r["status"] for r in rows}
    assert statuses["/a.md"] == "succeeded"  # c1's task drained
    assert statuses["/b.md"] == "pending"  # c2's task left for c2's own loop
    assert plugin.processed == ["/a.md"]  # the wrong-connector task was never read
    await eng.meta.close()


async def test_concurrent_loops_drain_both_jobs(tmp_path):
    eng = await _build_engine(tmp_path)
    cid, connector_uri = "cShared", "file:///repo"
    texts = {}
    for i in range(4):
        texts[f"/a{i}.md"] = f"# A{i}\n\nbody"
        texts[f"/b{i}.md"] = f"# B{i}\n\nbody"
    plugin = _FakeDocPlugin(texts)
    t = 0
    for i in range(4):  # interleave A and B insertions
        t += 1
        await _seed(
            eng,
            job_id="jobA",
            cid=cid,
            object_uri=f"/a{i}.md",
            started_at=f"2026-01-01T00:00:{t:02d}",
        )
        t += 1
        await _seed(
            eng,
            job_id="jobB",
            cid=cid,
            object_uri=f"/b{i}.md",
            started_at=f"2026-01-01T00:00:{t:02d}",
        )

    # concurrency = 2: two loops claim from the global pending pool (conditional UPDATE keeps
    # them from double-claiming). Both own jobA; with the global claim they together drain
    # jobB too.
    await asyncio.wait_for(
        asyncio.gather(
            eng._run_job_loop("jobA", cid, connector_uri, plugin, threshold=5, consec_fail=0),
            eng._run_job_loop("jobA", cid, connector_uri, plugin, threshold=5, consec_fail=0),
        ),
        timeout=15,
    )
    await eng._embed_consumer.shutdown()

    rows = await eng.meta.fetchall("SELECT object_uri, status FROM object_tasks")
    assert len(rows) == 8
    assert all(r["status"] == "succeeded" for r in rows)
    # every task processed exactly once (no double-claim across the two loops)
    assert sorted(plugin.processed) == sorted(texts)
    assert len(plugin.processed) == 8
    await eng.meta.close()
