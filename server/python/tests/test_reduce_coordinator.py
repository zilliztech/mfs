"""Unit tests for ReduceCoordinator pending accounting — early successes + crash recovery."""

from __future__ import annotations

import asyncio

from mfs_server.config import ServerConfig
from mfs_server.engine.reduce import ReduceCoordinator


class _FakePlugin:
    pass


def _coord():
    cfg = ServerConfig()
    cfg.summary.enabled = True
    return ReduceCoordinator(
        cfg, tx_cache=None, summary=None, vlm=None, converter=None, chunks_q=asyncio.Queue()
    )


async def test_early_success_before_sync_done_is_replayed():
    # The global-claim pump can finalize a Map task before on_sync_done fires. The decrement
    # must be stashed and replayed at sync-done, not dropped (finding 2).
    coord = _coord()
    coord.register_job("j", "file:///r", _FakePlugin())
    coord.on_yield_object_change("j", "/sub/a.md", "document")
    coord.on_yield_object_change("j", "/sub/b.md", "document")
    node = coord.builders["j"].tree["/sub"]
    assert node.pending == 2

    # a.md succeeds BEFORE enumeration finishes -> stashed, not applied yet
    coord.on_embed_succeeded("file:///r/sub/a.md", "j")
    assert node.pending == 2
    assert coord._early_succeeded.get("j") == ["/sub/a.md"]

    # sync_done replays the early decrement
    coord.on_sync_done("j")
    assert node.pending == 1
    assert "j" not in coord._early_succeeded  # drained

    # b.md succeeds after sync_done -> normal path; /sub now ready and pushed
    coord.on_embed_succeeded("file:///r/sub/b.md", "j")
    assert node.pending == 0
    assert coord.queue.job_queues.get("j")  # /sub queued for summarization


async def test_recover_job_predecrements_all_terminal_statuses():
    # Crash recovery must pre-decrement for every terminal status, not only 'succeeded' — a
    # failed/cancelled/skipped child never re-runs through Map (finding 5).
    coord = _coord()
    objects = [
        ("/sub/a.md", "document", "succeeded"),
        ("/sub/b.md", "document", "failed"),
        ("/sub/c.md", "document", "cancelled"),
        ("/sub/d.md", "document", "skipped"),
    ]
    coord.recover_job("j", "file:///r", _FakePlugin(), objects, existing_summaries=[])

    node = coord.builders["j"].tree["/sub"]
    assert node.pending == 0  # all four terminal children pre-decremented
    # /sub is ready -> queued; without the fix only 'succeeded' would decrement and it'd hang
    assert coord.queue.job_queues.get("j")
