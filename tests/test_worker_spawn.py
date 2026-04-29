"""Regression test for the detached worker spawn command.

The worker module lives at `mfs.ingest.worker`. Spawning it under the old
top-level path `mfs.worker` (which doesn't exist) silently breaks async
indexing — the subprocess crashes with `No module named mfs.worker` and
the queue never drains. Pin the module path here so a future move is
caught immediately.
"""

from __future__ import annotations

import importlib
import sys
from unittest.mock import patch

from mfs.config import Config


def test_worker_spawn_uses_ingest_worker_module(mfs_home, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    import mfs.ingest.worker as worker_mod
    importlib.reload(worker_mod)

    captured: dict = {}

    class _FakeProc:
        pid = 4242

    def _fake_popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _FakeProc()

    config = Config()
    w = worker_mod.Worker(config)
    with patch.object(worker_mod.subprocess, "Popen", side_effect=_fake_popen):
        pid = w.spawn()

    assert pid == 4242
    assert captured["args"] == [sys.executable, "-m", "mfs.ingest.worker", "--run"]
    # Sanity: the module can be located by Python at the path we pass.
    spec = importlib.util.find_spec("mfs.ingest.worker")
    assert spec is not None, "mfs.ingest.worker must be importable for spawn to work"


def test_worker_module_constant_matches_actual_module_path():
    import mfs.ingest.worker as worker_mod

    assert worker_mod.Worker.WORKER_MODULE == "mfs.ingest.worker"
    assert worker_mod.__name__ == worker_mod.Worker.WORKER_MODULE
