"""Spec for the business-named ConcurrencyGate wrappers (§5.5) and their process-wide
sharing between the Map producers and the Reduce SummaryWorker pool."""

from __future__ import annotations

import asyncio

from mfs_server.config import ServerConfig
from mfs_server.engine.engine import Engine
from mfs_server.engine.producers.base import (
    ConcurrencyGate,
    DescriptionConcurrencyGate,
    SummaryConcurrencyGate,
)


def test_named_gates_are_concurrency_gates():
    assert issubclass(DescriptionConcurrencyGate, ConcurrencyGate)
    assert issubclass(SummaryConcurrencyGate, ConcurrencyGate)
    assert isinstance(DescriptionConcurrencyGate(3), ConcurrencyGate)


def test_no_public_sem_alias():
    # call sites read `async with gate`, never a public `.sem` — the semaphore is private.
    g = DescriptionConcurrencyGate(2)
    assert not hasattr(g, "sem")
    assert hasattr(g, "_sem")  # private impl detail only


async def test_gate_caps_in_flight():
    g = SummaryConcurrencyGate(2)
    inflight = 0
    peak = 0

    async def work():
        nonlocal inflight, peak
        async with g:
            inflight += 1
            peak = max(peak, inflight)
            await asyncio.sleep(0.02)
            inflight -= 1

    await asyncio.gather(*[work() for _ in range(8)])
    assert peak <= 2


async def test_different_gates_do_not_cross_block():
    # holding a description gate at capacity must NOT block acquiring a summary gate
    g_desc = DescriptionConcurrencyGate(1)
    g_summ = SummaryConcurrencyGate(1)
    async with g_desc:  # g_desc now at capacity
        await asyncio.wait_for(g_summ.__aenter__(), timeout=0.5)  # independent -> acquires
        await g_summ.__aexit__(None, None, None)


async def test_engine_shares_one_gate_across_map_and_reduce(tmp_path):
    # the step-12 wiring: ONE description gate + ONE summary gate per process, shared by the
    # Map ProducerContext and the Reduce coordinator, so the in-flight budget is unified.
    cfg = ServerConfig()
    cfg.metadata.backend = "sqlite"
    cfg.metadata.path = str(tmp_path / "m.db")
    cfg.transformation_cache.backend = "sqlite"
    cfg.transformation_cache.db_path = str(tmp_path / "t.db")
    cfg.artifact_cache.root = str(tmp_path / "a")
    eng = Engine(cfg)
    await eng.meta.connect()
    await eng.meta.init_schema()
    eng._build_pipeline()
    try:
        assert eng._producer_ctx.summary_gate is eng._job_lane.summary_gate
        assert eng._producer_ctx.description_gate is eng._job_lane.description_gate
        assert isinstance(eng._job_lane.summary_gate, SummaryConcurrencyGate)
        assert isinstance(eng._job_lane.description_gate, DescriptionConcurrencyGate)
    finally:
        await eng._job_lane.stop()
        await eng._embed_consumer.shutdown()
        await eng.meta.close()
