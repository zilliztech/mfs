"""Unit tests for reduce/queue.py — per-job bottom-up heapq + cross-job round-robin."""

from __future__ import annotations

import asyncio

from mfs_server.engine.job_lane.queue import SummaryQueue


async def _drain(q: SummaryQueue, n: int) -> list[tuple]:
    """Run the dispatcher and collect n items from ready_q, then stop it."""
    disp = asyncio.create_task(q.dispatcher())
    out = []
    for _ in range(n):
        out.append(await asyncio.wait_for(q.ready_q.get(), timeout=2))
    disp.cancel()
    try:
        await disp
    except asyncio.CancelledError:
        pass
    return out


async def test_within_job_bottom_up_by_depth():
    q = SummaryQueue()
    q.push("A", "/a", 1)
    q.push("A", "/a/b/c", 3)
    q.push("A", "/a/b", 2)
    order = [d for _, d in await _drain(q, 3)]
    # deepest first (bottom-up): depth 3, then 2, then 1
    assert order == ["/a/b/c", "/a/b", "/a"]


async def test_cross_job_round_robin_no_starvation():
    q = SummaryQueue()
    # job A has only deep dirs; job B has only shallow dirs. Round-robin must interleave them
    # so A's deep tree cannot starve B's shallow tree.
    for i in range(3):
        q.push("A", f"/a/deep/{i}", 5)
    for i in range(3):
        q.push("B", f"/b/{i}", 1)
    items = await _drain(q, 6)
    jobs = [j for j, _ in items]
    # one from each job per dispatcher pass -> alternating job ids
    assert jobs[0] != jobs[1]
    assert jobs.count("A") == 3 and jobs.count("B") == 3
    # no job appears twice before the other has been served (strict interleave)
    assert jobs in (["A", "B", "A", "B", "A", "B"], ["B", "A", "B", "A", "B", "A"])


async def test_evict_job_clears_pending():
    q = SummaryQueue()
    q.push("A", "/a", 1)
    q.push("B", "/b", 1)
    q.evict_job("A")
    assert "A" not in q.job_queues and "A" not in q.job_rotation
    items = await _drain(q, 1)
    assert items == [("B", "/b")]
