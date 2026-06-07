"""Unit tests for TableSchemaProducer — schema_summary chunk via the summary gate."""

from __future__ import annotations

import asyncio

from mfs_server.engine.producers import Chunk, EndOfTask, ObjectTask, TableSchemaProducer

from _fakes import FakeArtifactStore, FakePlugin, FakeSummary, build_ctx, collect


def _task(plugin, uri="/public.users"):
    return ObjectTask(
        object_uri=uri,
        connector_uri="postgres://db",
        okind="table_schema",
        connector_job_id="job1",
        plugin=plugin,
    )


async def test_schema_summary_chunk(tmp_path):
    schema = {"table": "users", "columns": [{"name": "id", "type": "int"}]}
    plugin = FakePlugin(records={"/public.users": [schema]})
    summ = FakeSummary(reply="users table holds accounts")
    ctx = build_ctx(artifacts=FakeArtifactStore(tmp_path), summary=summ)
    items = await collect(TableSchemaProducer(ctx), _task(plugin))

    chunks = [x for x in items if isinstance(x, Chunk)]
    assert len(chunks) == 1
    c = chunks[0]
    assert c.chunk_kind == "schema_summary"
    assert c.locator is None
    assert c.content == "users table holds accounts"
    assert c.uri == "postgres://db/public.users" and c.connector_job_id == "job1"
    assert summ.calls == 1
    assert plugin.aclosed == ["/public.users"]  # generator closed after first record
    assert isinstance(items[-1], EndOfTask)


async def test_empty_summary_yields_no_chunk(tmp_path):
    plugin = FakePlugin(records={"/public.users": [{"table": "users"}]})
    ctx = build_ctx(artifacts=FakeArtifactStore(tmp_path), summary=FakeSummary(reply="  "))
    items = await collect(TableSchemaProducer(ctx), _task(plugin))
    assert len(items) == 1 and isinstance(items[0], EndOfTask)


async def test_no_records_ends_cleanly(tmp_path):
    plugin = FakePlugin(records={})  # read_records returns None
    ctx = build_ctx(artifacts=FakeArtifactStore(tmp_path))
    items = await collect(TableSchemaProducer(ctx), _task(plugin))
    assert len(items) == 1 and isinstance(items[0], EndOfTask)


async def test_summary_gate_caps_in_flight(tmp_path):
    summ = FakeSummary(delay=0.03)
    ctx = build_ctx(artifacts=FakeArtifactStore(tmp_path), summary_concurrency=2, summary=summ)
    plugins = [FakePlugin(records={f"/t{i}": [{"table": f"t{i}"}]}) for i in range(6)]
    prod = TableSchemaProducer(ctx)
    await asyncio.gather(*[collect(prod, _task(plugins[i], f"/t{i}")) for i in range(6)])
    assert summ.max_inflight <= 2
    assert summ.calls == 6
