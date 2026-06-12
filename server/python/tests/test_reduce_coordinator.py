"""Unit tests for JobLaneCoordinator scheduling — files do not gate dirs + crash recovery."""

from __future__ import annotations

import asyncio

from mfs_server.config import ServerConfig
from mfs_server.engine.job_lane import JobLaneCoordinator


class _FakePlugin:
    pass


def _coord():
    cfg = ServerConfig()
    cfg.summary.enabled = True
    return JobLaneCoordinator(
        cfg, tx_cache=None, summary=None, vlm=None, converter=None, chunks_q=asyncio.Queue()
    )


async def test_files_do_not_gate_dir_pushed_at_sync_done():
    # A dir folds its children's source content, not their embeddings, so files do not gate
    # it: it is ready to summarize the moment enumeration completes (parallel with the Object
    # Lane), and a file's embed success is ignored by the reduce coordinator.
    coord = _coord()
    coord.register_job("j", "file:///r", _FakePlugin())
    coord.on_yield_object_change("j", "/sub/a.md", "document")
    coord.on_yield_object_change("j", "/sub/b.md", "document")
    node = coord.builders["j"].tree["/sub"]
    assert node.pending == 0  # files don't increment pending

    # a file's embed success is a no-op for reduce (not a dir uri) and pushes nothing
    coord.on_embed_succeeded("file:///r/sub/a.md", "j")
    assert node.pending == 0
    assert not coord.queue.job_queues.get("j")  # nothing queued before enumeration completes

    # on_sync_done finalizes the tree and pushes the leaf dir right away — no wait for files
    coord.on_sync_done("j")
    queued = [el[2] for el in coord.queue.job_queues.get("j", [])]
    assert queued == ["/sub"]


async def test_recover_job_pushes_leaf_dirs_and_seeds_existing_summaries():
    # Crash recovery: files do not gate, so leaf dirs are ready immediately; a dir whose
    # summary already reached Milvus is seeded (and counted persisted), not recomputed.
    coord = _coord()
    objects = [
        ("/sub/a.md", "document", "succeeded"),
        ("/sub/b.md", "document", "failed"),
        ("/done/c.md", "document", "succeeded"),
    ]
    coord.recover_job(
        "j",
        "file:///r",
        _FakePlugin(),
        objects,
        existing_summaries=[("/done", "old summary")],
    )
    builder = coord.builders["j"]
    assert builder.tree["/sub"].pending == 0  # files don't gate; leaf ready
    assert builder.tree["/done"].summary == "old summary"  # seeded, not recomputed
    # /sub still needs summarizing -> queued; /done already done -> not queued
    queued = [el[2] for el in coord.queue.job_queues.get("j", [])]
    assert "/sub" in queued and "/done" not in queued
