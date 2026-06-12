"""Unit tests for ImageChunksProducer — gate semaphore + tx_cache hit/miss + artifact."""

from __future__ import annotations

import asyncio

from mfs_server.engine.producers import Chunk, EndOfTask, ObjectTask, ImageChunksProducer

from _fakes import FakeArtifactStore, FakePlugin, FakeVlm, build_ctx, collect


def _task(uri, plugin, job="job1"):
    return ObjectTask(
        object_uri=uri,
        connector_uri="file:///r",
        okind="image",
        connector_job_id=job,
        plugin=plugin,
    )


async def test_image_produces_vlm_description(tmp_path):
    store = FakeArtifactStore(tmp_path)
    plugin = FakePlugin(data={"/cat.png": b"\x89PNGdata"})
    ctx = build_ctx(artifacts=store)
    items = await collect(ImageChunksProducer(ctx), _task("/cat.png", plugin))

    chunks = [x for x in items if isinstance(x, Chunk)]
    assert len(chunks) == 1
    c = chunks[0]
    assert c.chunk_kind == "vlm_description"
    assert c.locator is None
    assert c.uri == "file:///r/cat.png" and c.connector_job_id == "job1"
    assert isinstance(items[-1], EndOfTask)
    # The description is a model output: it lives in the transformation cache (via the VLM
    # client), not as an artifact. The producer no longer writes a vlm_text artifact.
    assert await store.get_artifact("default", "file:///r/cat.png", "vlm_text") is None


async def test_description_gate_caps_in_flight(tmp_path):
    vlm = FakeVlm(delay=0.03)
    ctx = build_ctx(artifacts=FakeArtifactStore(tmp_path), description_concurrency=2, vlm=vlm)
    # 6 distinct images processed concurrently; gate must keep <= 2 VLM calls in flight
    plugins = [FakePlugin(data={f"/img{i}.png": f"bytes-{i}".encode()}) for i in range(6)]
    prod = ImageChunksProducer(ctx)
    await asyncio.gather(*[collect(prod, _task(f"/img{i}.png", plugins[i])) for i in range(6)])
    assert vlm.max_inflight <= 2
    assert vlm.api_calls == 6


async def test_tx_cache_hit_on_identical_image(tmp_path):
    vlm = FakeVlm()
    ctx = build_ctx(artifacts=FakeArtifactStore(tmp_path), vlm=vlm)
    prod = ImageChunksProducer(ctx)
    same = {"/a.png": b"identical"}
    await collect(prod, _task("/a.png", FakePlugin(data=same)))
    await collect(prod, _task("/a.png", FakePlugin(data=same)))
    # second describe() of the same bytes is a cache hit, not a second API call
    assert vlm.api_calls == 1
    assert vlm.cache_hits == 1


async def test_empty_description_yields_no_chunk(tmp_path):
    ctx = build_ctx(artifacts=FakeArtifactStore(tmp_path), vlm=FakeVlm(reply="   "))
    plugin = FakePlugin(data={"/blank.png": b"x"})
    items = await collect(ImageChunksProducer(ctx), _task("/blank.png", plugin))
    assert len(items) == 1 and isinstance(items[0], EndOfTask)
