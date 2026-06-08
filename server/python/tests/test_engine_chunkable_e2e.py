"""End-to-end: engine drains document/code ObjectTasks through the NEW pipeline path.

Drives the real Engine worker loop (`_run_job_loop`) with a fake file connector and fake
Milvus / embedder / tx_cache (no live network, no real embedding model). Asserts the
producer -> chunks_q -> EmbedConsumer path embeds + upserts body chunks, preserves heading
boundaries, deletes once per object (§6.1), and flips tasks to 'succeeded' in sqlite.
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


class _FakeFilePlugin:
    """Minimal file connector: canned markdown text, document okind."""

    def __init__(self, files: dict[str, str]):
        self._files = files
        self.ctx = _FakeConnCtx()
        self.indexed: list[str] = []

    async def connect(self):
        return None

    async def stat(self, rel):
        data = self._files[rel]
        return PathStat(
            path=rel,
            type="file",
            media_type="text/markdown",
            size_hint=len(data.encode()),
            fingerprint="fp:" + rel,
        )

    def object_kind_of(self, rel):
        return "document"

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
        return "k:" + hashlib.sha1(text.encode()).hexdigest()

    async def _embed_api(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]


class _FakeMilvus:
    def __init__(self):
        self.upserts: list[tuple[str, list[dict]]] = []
        self.deletes: list[tuple[str, str, str]] = []

    def upsert(self, ns, rows):
        self.upserts.append((ns, rows))

    def delete_by_object(self, ns, connector_uri, object_uri):
        self.deletes.append((ns, connector_uri, object_uri))


class _FakeTxCache:
    async def batch_get(self, keys):
        return {k: None for k in keys}  # all-miss

    async def batch_put(self, entries):
        return None


_FILES = {
    "/readme.md": "# Title\n\nIntro paragraph with enough words to fill a chunk budget.\n\n"
    "## Setup\n\nSteps for setup go here with more words to spill over.\n\n"
    "## Usage\n\nHow to use it, described across a sentence or two.",
    "/guide.md": "# Guide\n\nGuide intro text that is reasonably long here.\n\n"
    "## Details\n\nDetailed body content that should land in its own chunk.",
    "/api.md": "# API\n\nThe API overview paragraph sits under the first heading.\n\n"
    "## Endpoints\n\nEndpoint descriptions live under this second heading.",
}


async def _build_engine(tmp_path):
    cfg = ServerConfig()
    cfg.metadata.backend = "sqlite"
    cfg.metadata.path = str(tmp_path / "meta.db")
    cfg.transformation_cache.backend = "sqlite"
    cfg.transformation_cache.db_path = str(tmp_path / "tx.db")
    cfg.artifact_cache.root = str(tmp_path / "art")
    cfg.chunking.chunk_size = 32  # small budget forces multi-heading docs to split per heading
    eng = Engine(cfg)
    eng.embed = _FakeEmbed()
    eng.milvus = _FakeMilvus()
    eng.tx_cache = _FakeTxCache()
    eng._embed_idle_ms = 50  # responsive idle flush so the small job drains fast
    await eng.meta.connect()
    await eng.meta.init_schema()
    await eng.meta.execute("PRAGMA foreign_keys=OFF")  # seed object_tasks without parent rows
    eng._build_pipeline()
    return eng


async def _seed(eng, *, job_id, cid):
    for rel in _FILES:
        await eng.meta.execute(
            "INSERT INTO object_tasks (id, connector_job_id, connector_id, object_uri, old_uri, "
            " change_kind, status, priority, attempts) VALUES (?,?,?,?,?,?,?,?,0)",
            (uuid.uuid4().hex, job_id, cid, rel, None, "added", "pending", 0),
        )


async def test_chunkable_path_drains_through_pipeline(tmp_path):
    eng = await _build_engine(tmp_path)
    job_id, cid, connector_uri = "job1", "cA", "file:///repo"
    await _seed(eng, job_id=job_id, cid=cid)
    plugin = _FakeFilePlugin(_FILES)

    await asyncio.wait_for(
        eng._run_job_loop(job_id, cid, connector_uri, plugin, threshold=5, consec_fail=0),
        timeout=10,
    )
    await eng._embed_consumer.shutdown()

    # all chunk rows are body chunks, routed through chunks_q to the (fake) Milvus sink
    all_rows = [r for _, rows in eng.milvus.upserts for r in rows]
    assert all_rows, "expected body chunks to be upserted"
    assert all(r["chunk_kind"] == "body" for r in all_rows)
    assert all(r["dense_vec"] == [0.1, 0.2, 0.3] for r in all_rows)
    assert {r["object_uri"] for r in all_rows} == {
        "file:///repo/readme.md",
        "file:///repo/guide.md",
        "file:///repo/api.md",
    }

    # markdown heading boundaries preserved: each doc split into >1 chunk and headings start
    # chunks rather than being buried mid-chunk.
    readme_chunks = [r["content"] for r in all_rows if r["object_uri"].endswith("/readme.md")]
    assert len(readme_chunks) >= 2
    starts = [c.lstrip() for c in readme_chunks]
    assert any(s.startswith("## Setup") or s.startswith("Setup") for s in starts)
    assert any(s.startswith("## Usage") or s.startswith("Usage") for s in starts)
    for c in readme_chunks:
        assert c.count("## Setup") + c.count("## Usage") <= 1  # no chunk swallows two headings

    # per-object atomic: delete_by_object exactly once per object (not per chunk)
    deleted = [(cu, ou) for _, cu, ou in eng.milvus.deletes]
    assert sorted(deleted) == sorted(
        [
            ("file:///repo", "file:///repo/readme.md"),
            ("file:///repo", "file:///repo/guide.md"),
            ("file:///repo", "file:///repo/api.md"),
        ]
    )

    # every task transitioned to 'succeeded'
    rows = await eng.meta.fetchall("SELECT object_uri, status FROM object_tasks")
    assert {r["object_uri"]: r["status"] for r in rows} == {
        "/readme.md": "succeeded",
        "/guide.md": "succeeded",
        "/api.md": "succeeded",
    }

    # objects table rows written by the shared _index_object tail
    obj = await eng.meta.fetchall("SELECT object_uri, search_status, chunk_count FROM objects")
    assert {o["object_uri"] for o in obj} == {"/readme.md", "/guide.md", "/api.md"}
    assert all(o["search_status"] == "indexed" and o["chunk_count"] >= 1 for o in obj)
    assert sorted(plugin.indexed) == ["/api.md", "/guide.md", "/readme.md"]

    await eng.meta.close()


async def test_empty_document_marks_not_indexed_and_purges(tmp_path):
    eng = await _build_engine(tmp_path)
    eng._files = {}
    files = {"/empty.md": "   \n  "}
    plugin = _FakeFilePlugin(files)
    job_id, cid, connector_uri = "job2", "cB", "file:///repo"
    await eng.meta.execute(
        "INSERT INTO object_tasks (id, connector_job_id, connector_id, object_uri, old_uri, "
        " change_kind, status, priority, attempts) VALUES (?,?,?,?,?,?,?,?,0)",
        (uuid.uuid4().hex, job_id, cid, "/empty.md", None, "added", "pending", 0),
    )

    await asyncio.wait_for(
        eng._run_job_loop(job_id, cid, connector_uri, plugin, threshold=5, consec_fail=0),
        timeout=10,
    )
    await eng._embed_consumer.shutdown()

    # zero chunks: still marked succeeded, objects row not_indexed, stale chunks purged
    row = await eng.meta.fetchone("SELECT status FROM object_tasks WHERE object_uri='/empty.md'")
    assert row["status"] == "succeeded"
    obj = await eng.meta.fetchone(
        "SELECT search_status, chunk_count FROM objects WHERE object_uri='/empty.md'"
    )
    assert obj["search_status"] == "not_indexed" and obj["chunk_count"] == 0
    assert ("file:///repo", "file:///repo/empty.md") in [
        (cu, ou) for _, cu, ou in eng.milvus.deletes
    ]
    assert eng.milvus.upserts == []  # nothing embedded for an empty doc
    await eng.meta.close()
