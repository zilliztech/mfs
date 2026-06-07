"""Unit tests for TransformationCache.get_or_compute — the per-key compute lock (§3.4)."""

from __future__ import annotations

import asyncio

import pytest

from mfs_server.config import ServerConfig
from mfs_server.storage.transformation_cache import make_transformation_cache


async def _cache(tmp_path):
    cfg = ServerConfig()
    cfg.transformation_cache.backend = "sqlite"
    cfg.transformation_cache.db_path = str(tmp_path / "tx.db")
    cache = make_transformation_cache(cfg)
    await cache.connect()
    return cache


async def test_single_miss_computes_once_and_caches(tmp_path):
    cache = await _cache(tmp_path)
    calls = 0

    async def compute():
        nonlocal calls
        calls += 1
        return b"value-1"

    r1 = await cache.get_or_compute("k1", compute, kind="vlm", provider="openai")
    assert r1 == b"value-1"
    assert calls == 1
    # second call hits the cache (fast path) — compute not invoked again
    r2 = await cache.get_or_compute("k1", compute)
    assert r2 == b"value-1"
    assert calls == 1
    # stored row carries the metadata via the normal batch_get path
    assert (await cache.batch_get(["k1"]))["k1"] == b"value-1"
    await cache.close()


async def test_concurrent_same_key_computes_exactly_once(tmp_path):
    cache = await _cache(tmp_path)
    calls = 0
    inflight = 0
    peak = 0

    async def compute():
        nonlocal calls, inflight, peak
        inflight += 1
        peak = max(peak, inflight)
        calls += 1
        await asyncio.sleep(0.02)
        inflight -= 1
        return b"once"

    results = await asyncio.gather(*[cache.get_or_compute("same", compute) for _ in range(50)])
    assert calls == 1  # the per-key lock + double-check collapsed 50 misses into one compute
    assert peak == 1  # never two computes for the same key in flight
    assert results == [b"once"] * 50
    await cache.close()


async def test_different_keys_compute_in_parallel(tmp_path):
    cache = await _cache(tmp_path)
    inflight = 0
    peak = 0

    def make(val):
        async def compute():
            nonlocal inflight, peak
            inflight += 1
            peak = max(peak, inflight)
            await asyncio.sleep(0.05)
            inflight -= 1
            return val

        return compute

    r = await asyncio.gather(
        cache.get_or_compute("ka", make(b"a")),
        cache.get_or_compute("kb", make(b"b")),
        cache.get_or_compute("kc", make(b"c")),
    )
    assert r == [b"a", b"b", b"c"]
    assert peak == 3  # distinct keys use distinct locks -> computes overlap, not serialized
    await cache.close()


async def test_double_check_skips_compute_when_filled_during_wait(tmp_path):
    cache = await _cache(tmp_path)
    entered = asyncio.Event()
    second_calls = 0

    async def slow_first():
        entered.set()
        await asyncio.sleep(0.05)  # hold the lock while the second caller arrives + waits
        return b"first"

    async def must_not_run():
        nonlocal second_calls
        second_calls += 1
        return b"second"

    t1 = asyncio.create_task(cache.get_or_compute("dup", slow_first))
    await entered.wait()  # first is inside compute, holding the lock, not yet stored
    # second misses the fast path (nothing stored yet), waits on the lock, and after the
    # first stores it the double-check returns the cached value -> its compute never runs
    r2 = await cache.get_or_compute("dup", must_not_run)
    r1 = await t1
    assert r1 == b"first" and r2 == b"first"
    assert second_calls == 0
    await cache.close()


async def test_compute_error_releases_lock_and_does_not_cache(tmp_path):
    cache = await _cache(tmp_path)

    async def boom():
        raise ValueError("compute failed")

    with pytest.raises(ValueError, match="compute failed"):
        await cache.get_or_compute("err", boom)
    # nothing cached after a failed compute
    assert (await cache.batch_get(["err"]))["err"] is None

    # lock was released (not deadlocked): a retry with a working compute succeeds
    async def good():
        return b"recovered"

    assert await cache.get_or_compute("err", good) == b"recovered"
    assert (await cache.batch_get(["err"]))["err"] == b"recovered"
    await cache.close()


async def test_batch_get_put_unchanged(tmp_path):
    cache = await _cache(tmp_path)
    await cache.batch_put(
        [
            {
                "cache_key": "bk",
                "kind": "embedding",
                "input_hash": "h",
                "provider": "onnx",
                "model": "m",
                "model_version": "1",
                "output_bytes": b"vec-bytes",
                "output_size": 9,
            }
        ]
    )
    got = await cache.batch_get(["bk", "missing"])
    assert got["bk"] == b"vec-bytes"
    assert got["missing"] is None
    await cache.close()
