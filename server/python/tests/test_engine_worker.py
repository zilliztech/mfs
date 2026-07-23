"""Independent unit tests for WorkerScheduler: constructed without an Engine,
injecting fakes. Covers _resolve_concurrency and the queue-empty short-circuit
in run_worker_once. The two-layer try/except and the full run_worker_once /
run_forever loops are exercised by the existing e2e suite.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

from mfs_server.config import ServerConfig
from mfs_server.engine.worker import WorkerScheduler


class _FakeObjects:
    def __init__(self, job=None):
        self._job = job

    async def claim_queued_job(self):
        return self._job


def _build_worker(objects=None, cfg=None):
    cfg = cfg or ServerConfig()
    return WorkerScheduler(
        cfg, SimpleNamespace(), SimpleNamespace(), objects or _FakeObjects(), SimpleNamespace()
    )


def test_resolve_concurrency_auto_uses_cpu_count():
    w = _build_worker()
    assert w._resolve_concurrency("auto") == max(1, (os.cpu_count() or 2))


def test_resolve_concurrency_explicit_int():
    w = _build_worker()
    assert w._resolve_concurrency(5) == 5
    assert w._resolve_concurrency(0) == 1  # max(1, 0)


def test_resolve_concurrency_invalid_falls_back_to_one():
    w = _build_worker()
    assert w._resolve_concurrency("not-a-number") == 1


def test_resolve_concurrency_none_uses_cfg_default():
    cfg = ServerConfig()
    expected = cfg.chunks_producer.concurrency
    w = _build_worker(cfg=cfg)
    if expected == "auto":
        assert w._resolve_concurrency(None) == max(1, (os.cpu_count() or 2))
    else:
        assert w._resolve_concurrency(None) == max(1, int(expected))


async def test_run_worker_once_returns_none_when_queue_empty():
    w = _build_worker(objects=_FakeObjects(job=None))
    assert await w.run_worker_once() is None
