"""Unit tests for reduce/worker.py — fold children, summarize, bookkeeping, emit."""

from __future__ import annotations

import asyncio

from mfs_server.config import ServerConfig
from mfs_server.engine.producers.base import Chunk, EndOfTask
from mfs_server.engine.reduce import ReduceCoordinator
from mfs_server.engine.reduce.worker import fold_and_summarize


class _FakePlugin:
    def __init__(self, files):
        self._files = files  # rel -> bytes

    async def read(self, rel, range=None):
        yield self._files[rel]


class _FakeSummary:
    def __init__(self):
        self.inputs: list[str] = []

    async def summarize(self, text, kind="directory_summary"):
        self.inputs.append(text)
        return f"SUMMARY({len(text)})"


class _FakeVlm:
    provider = "openai"
    model = "gpt-4o-mini"
    version = "1"

    def __init__(self):
        self.calls = 0

    async def describe(self, data, ext):
        self.calls += 1
        return "image description"


class _FakeConverter:
    provider = "markitdown"
    default = "markitdown"
    version = "1"

    async def convert(self, data, ext):
        return "converted markdown"


class _FakeTx:
    """The worker no longer calls get_or_compute directly (single-flight lives inside the vlm /
    converter clients now); this stub exists only so the coordinator constructs."""

    async def get_or_compute(self, cache_key, compute_fn, **kw):
        return await compute_fn()


def _coord(files, *, include_image_desc=False, description_enabled=True):
    cfg = ServerConfig()
    cfg.summary.enabled = True
    cfg.summary.include_image_description = include_image_desc
    cfg.description.enabled = description_enabled
    coord = ReduceCoordinator(
        cfg,
        tx_cache=_FakeTx(),
        summary=_FakeSummary(),
        vlm=_FakeVlm(),
        converter=_FakeConverter(),
        chunks_q=asyncio.Queue(),
    )
    coord.register_job("j", "file:///r", _FakePlugin(files))
    return coord


async def test_fold_document_leaf_summarizes_and_emits():
    coord = _coord({"/sub/f.md": b"# Title\n\nbody text"})
    b = coord.builders["j"]
    b.add("/sub/f.md", "document")
    b.sync_done = True

    await fold_and_summarize(coord, "j", "/sub")

    node = b.tree["/sub"]
    # summary computed from the child's content + written back to the node
    assert coord.summary.inputs and "body text" in coord.summary.inputs[0]
    assert node.summary == f"SUMMARY({len(coord.summary.inputs[0])})"

    # parent pending decremented; reached 0 -> root pushed onto the job's queue
    assert b.tree["/"].pending == 0
    assert coord.queue.job_queues.get("j")  # root queued

    # a directory_summary chunk (+ EndOfTask) emitted into chunks_q
    env1 = coord.chunks_q.get_nowait()
    env2 = coord.chunks_q.get_nowait()
    assert isinstance(env1.payload, Chunk)
    assert env1.payload.chunk_kind == "directory_summary"
    assert env1.task_uri == "file:///r/sub"
    assert env1.job_id == "j"
    assert env1.payload.uri == "file:///r/sub"
    assert isinstance(env2.payload, EndOfTask)


async def test_parent_folds_subdir_summaries():
    coord = _coord({"/sub1/a.md": b"alpha", "/sub2/b.md": b"beta"})
    b = coord.builders["j"]
    b.add("/sub1/a.md", "document")
    b.add("/sub2/b.md", "document")
    b.sync_done = True

    # summarize both leaves -> each decrements root pending; second one pushes root
    await fold_and_summarize(coord, "j", "/sub1")
    await fold_and_summarize(coord, "j", "/sub2")
    assert b.tree["/"].pending == 0

    # now the root folds the two sub-dir summaries
    coord.summary.inputs.clear()
    await fold_and_summarize(coord, "j", "/")
    root_input = coord.summary.inputs[0]
    assert "/sub1" in root_input and "/sub2" in root_input
    assert b.tree["/"].summary is not None
    assert b.tree["/"].parent is None  # root has no parent to notify


async def test_image_child_described_and_folded():
    coord = _coord({"/d/pic.png": b"\x89PNGbytes"}, include_image_desc=True)
    b = coord.builders["j"]
    b.add("/d/pic.png", "image")
    b.sync_done = True

    await fold_and_summarize(coord, "j", "/d")
    # the image child was described once (single-flight now lives inside the vlm client) and
    # its description folded into the directory summary input.
    assert coord.vlm.calls == 1
    assert "image description" in coord.summary.inputs[0]


async def test_image_child_skipped_when_description_disabled():
    # finding (15): with [description] off there is no VLM provider/budget, so a folded-in
    # image must NOT trigger a describe() call even if include_image_description is on.
    coord = _coord(
        {"/d/pic.png": b"\x89PNGbytes"}, include_image_desc=True, description_enabled=False
    )
    b = coord.builders["j"]
    b.add("/d/pic.png", "image")
    b.sync_done = True

    await fold_and_summarize(coord, "j", "/d")
    assert coord.vlm.calls == 0  # VLM never invoked
