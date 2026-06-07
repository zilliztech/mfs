"""SummaryQueue — per-job heapq + cross-job round-robin dispatcher (§6.4.2).

Each job has its own heapq keyed on (-depth, monotonic_t, dir_uri) so the deepest ready
dir pops first (bottom-up). A dispatcher coroutine fans the heaps into a single ready_q,
taking ONE entry from each active job per pass, so a job with a deep tree cannot starve
another job's shallow tree.
"""

from __future__ import annotations

import asyncio
import heapq
import time
from collections import deque


class SummaryQueue:
    def __init__(self):
        self.job_queues: dict[str, list] = {}  # job_id -> heapq[(-depth, monotonic_t, dir_uri)]
        self.job_rotation: deque[str] = deque()  # active jobs, round-robined by dispatcher
        self.ready_q: asyncio.Queue = asyncio.Queue()  # fair fan-in to the worker pool
        self.new_work = asyncio.Event()

    def push(self, job_id: str, dir_uri: str, depth: int) -> None:
        heapq.heappush(self.job_queues.setdefault(job_id, []), (-depth, time.monotonic(), dir_uri))
        if job_id not in self.job_rotation:
            self.job_rotation.append(job_id)
        self.new_work.set()

    async def dispatcher(self) -> None:
        """Round-robin one ready dir from each active job into ready_q; idle-wait on
        new_work when every heap is empty."""
        while True:
            advanced = False
            for job_id in list(self.job_rotation):
                q = self.job_queues.get(job_id)
                if q:
                    _, _, dir_uri = heapq.heappop(q)
                    await self.ready_q.put((job_id, dir_uri))
                    advanced = True
                elif job_id in self.job_rotation:
                    self.job_rotation.remove(job_id)
            if not advanced:
                self.new_work.clear()
                await self.new_work.wait()

    def evict_job(self, job_id: str) -> None:
        """Drop a terminal job's pending dirs + remove it from the rotation."""
        self.job_queues.pop(job_id, None)
        try:
            self.job_rotation.remove(job_id)
        except ValueError:
            pass
