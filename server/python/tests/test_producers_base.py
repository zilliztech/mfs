"""Unit tests for producers/base.py — Chunk / EndOfTask / ObjectTask / gates / dispatch."""

from __future__ import annotations

import asyncio

from mfs_server.connectors.base import ObjectConfig
from mfs_server.engine.producers import (
    CONTENT_MAX,
    ArtifactStore,
    Chunk,
    ChunksProducer,
    ConcurrencyGate,
    DescriptionConcurrencyGate,
    END_OF_TASK,
    EndOfTask,
    ImageChunksProducer,
    MessageStreamProducer,
    ObjectTask,
    RecordCollectionProducer,
    SummaryConcurrencyGate,
    TableSchemaProducer,
    TextChunksProducer,
    cap_content,
    select_producer,
)

from _fakes import FakeArtifactStore, build_ctx


def test_chunk_defaults():
    c = Chunk(content="hi", chunk_kind="body")
    assert c.locator is None
    assert c.metadata == {}
    assert c.uri is None and c.connector_job_id is None
    assert c.partial is False


def test_end_of_task_sentinel():
    assert isinstance(END_OF_TASK, EndOfTask)
    assert END_OF_TASK.partial is False
    # frozen value-equal to a fresh clean sentinel; a partial one differs
    assert END_OF_TASK == EndOfTask()
    assert EndOfTask(partial=True) != END_OF_TASK
    assert EndOfTask(partial=True).partial is True


def test_object_task_derived_fields():
    t = ObjectTask(object_uri="/a/b/file.PY", connector_uri="file:///repo", okind="code")
    assert t.full_uri == "file:///repo/a/b/file.PY"
    assert t.ext == ".py"  # lowercased
    assert isinstance(t.config(), ObjectConfig)  # default when ocfg is None
    oc = ObjectConfig(text_fields=["x"])
    assert ObjectTask(object_uri="/a", connector_uri="c", okind="code", ocfg=oc).config() is oc


def test_cap_content():
    assert cap_content("abc") == ("abc", False)
    big = "x" * (CONTENT_MAX + 10)
    capped, was = cap_content(big)
    assert was is True and len(capped) == CONTENT_MAX


async def test_concurrency_gate_limits_in_flight():
    gate = ConcurrencyGate(3)
    inflight = 0
    peak = 0

    async def worker():
        nonlocal inflight, peak
        async with gate:
            inflight += 1
            peak = max(peak, inflight)
            await asyncio.sleep(0.02)
            inflight -= 1

    await asyncio.gather(*[worker() for _ in range(12)])
    assert peak <= 3
    assert inflight == 0


def test_named_gates_are_concurrency_gates():
    assert issubclass(DescriptionConcurrencyGate, ConcurrencyGate)
    assert issubclass(SummaryConcurrencyGate, ConcurrencyGate)


def test_concurrency_gate_floor_is_one():
    # a 0/negative concurrency must not deadlock — floored to 1
    assert ConcurrencyGate(0)._sem._value == 1


def test_artifact_store_protocol(tmp_path):
    store = FakeArtifactStore(tmp_path)
    assert isinstance(store, ArtifactStore)  # runtime_checkable structural match


def test_select_producer_dispatch(tmp_path):
    ctx = build_ctx(artifacts=FakeArtifactStore(tmp_path))
    cases = {
        "document": TextChunksProducer,
        "code": TextChunksProducer,
        "text_blob": TextChunksProducer,
        "image": ImageChunksProducer,
        "message_stream": MessageStreamProducer,
        "table_rows": RecordCollectionProducer,
        "record_collection": RecordCollectionProducer,
        "table_schema": TableSchemaProducer,
    }
    for okind, cls in cases.items():
        p = select_producer(okind, ctx)
        assert isinstance(p, cls)
        assert isinstance(p, ChunksProducer)  # structural protocol conformance
    # okinds that carry no chunks dispatch to nothing
    assert select_producer("binary", ctx) is None
    assert select_producer("directory", ctx) is None
