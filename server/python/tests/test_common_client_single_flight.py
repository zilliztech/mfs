"""Map-side client single-flight + cache-key staleness (findings 10, 12).

The vlm / summary / converter clients route through tx_cache.get_or_compute, so concurrent
same-input callers (Map producer + Reduce worker) fire the provider once; and the cache key
folds in prompt / max_tokens so a config change re-computes instead of returning a stale value.
"""

from __future__ import annotations

import asyncio

from mfs_server.common.summary import CachingSummaryClient
from mfs_server.common.vlm import CachingVlmClient
from mfs_server.config import ServerConfig
from mfs_server.storage.transformation_cache import make_transformation_cache


async def _cache(tmp_path):
    cfg = ServerConfig()
    cfg.transformation_cache.backend = "sqlite"
    cfg.transformation_cache.db_path = str(tmp_path / "tx.db")
    cache = make_transformation_cache(cfg)
    await cache.connect()
    return cache


class _SlowVisionLLM:
    def __init__(self):
        self.calls = 0

    async def vision(self, prompt, data, mime, *, model, max_tokens):
        self.calls += 1
        await asyncio.sleep(0.05)  # widen the window so a missing lock would double-fire
        return f"desc:{prompt[:6]}"


async def test_vlm_concurrent_same_image_fires_provider_once(tmp_path):
    cache = await _cache(tmp_path)
    cfg = ServerConfig()
    vlm = CachingVlmClient(cfg, cache)
    llm = _SlowVisionLLM()
    vlm._llm = llm  # inject provider; skip lazy build

    data = b"\x89PNG-identical"
    # two concurrent callers, identical bytes -> single-flight via the §3.4 compute lock
    r1, r2 = await asyncio.gather(vlm.describe(data, ".png"), vlm.describe(data, ".png"))
    assert r1 == r2
    assert llm.calls == 1  # provider hit exactly once
    await cache.close()


async def test_vlm_prompt_change_invalidates_cache(tmp_path):
    # finding (10): the prompt is part of the cache identity.
    cache = await _cache(tmp_path)
    data = b"\x89PNG-bytes"

    cfg1 = ServerConfig()
    cfg1.description.prompt = "describe A"
    vlm1 = CachingVlmClient(cfg1, cache)
    vlm1._llm = _SlowVisionLLM()
    await vlm1.describe(data, ".png")
    assert vlm1.api_calls == 1

    cfg2 = ServerConfig()
    cfg2.description.prompt = "describe B"  # different prompt -> must re-describe
    vlm2 = CachingVlmClient(cfg2, cache)
    llm2 = _SlowVisionLLM()
    vlm2._llm = llm2
    await vlm2.describe(data, ".png")
    assert llm2.calls == 1  # not served from the prompt-A cache entry
    await cache.close()


class _SlowChatLLM:
    def __init__(self):
        self.calls = 0

    async def chat(self, prompt, *, model, max_tokens):
        self.calls += 1
        await asyncio.sleep(0.05)
        return f"summary({max_tokens})"


async def test_summary_max_tokens_change_invalidates_cache(tmp_path):
    # finding (10): max_tokens is part of the cache identity.
    cache = await _cache(tmp_path)
    text = "some directory listing content"

    cfg1 = ServerConfig()
    cfg1.summary.max_tokens = 128
    s1 = CachingSummaryClient(cfg1, cache)
    s1._llm = _SlowChatLLM()
    await s1.summarize(text, "directory_summary")
    assert s1.api_calls == 1

    cfg2 = ServerConfig()
    cfg2.summary.max_tokens = 512  # different budget -> must re-summarize
    s2 = CachingSummaryClient(cfg2, cache)
    llm2 = _SlowChatLLM()
    s2._llm = llm2
    await s2.summarize(text, "directory_summary")
    assert llm2.calls == 1
    await cache.close()
