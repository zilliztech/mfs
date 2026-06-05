"""Unit tests for RecordCollectionProducer — streaming, locators, head_cache, partial."""

from __future__ import annotations

import pytest

from mfs_server.connectors.base import ObjectConfig
from mfs_server.engine.producers import Chunk, EndOfTask, ObjectTask, RecordCollectionProducer

from _fakes import FakeArtifactStore, FakePlugin, build_ctx, collect

_OCFG = ObjectConfig(
    text_fields=["title", "body"],
    metadata_fields=["state"],
    locator_fields=["number"],
)


def _task(plugin, ocfg=_OCFG, uri="/issues"):
    return ObjectTask(
        object_uri=uri,
        connector_uri="gh://o/r",
        okind="record_collection",
        connector_job_id="job1",
        plugin=plugin,
        ocfg=ocfg,
    )


async def test_per_record_rows_with_locator_and_metadata(tmp_path):
    recs = [
        {"number": 1, "title": "bug", "body": "broken", "state": "open"},
        {"number": 2, "title": "feat", "body": "shiny", "state": "closed"},
    ]
    plugin = FakePlugin(records={"/issues": recs})
    ctx = build_ctx(artifacts=FakeArtifactStore(tmp_path))
    items = await collect(RecordCollectionProducer(ctx), _task(plugin))
    chunks = [x for x in items if isinstance(x, Chunk)]

    assert len(chunks) == 2
    assert all(c.chunk_kind == "row_text" for c in chunks)
    assert chunks[0].locator == {"number": 1}
    assert chunks[0].metadata == {"state": "open"}
    assert "title: bug" in chunks[0].content and "body: broken" in chunks[0].content
    assert plugin.aclosed == ["/issues"]  # cursor released
    assert isinstance(items[-1], EndOfTask) and items[-1].partial is False


async def test_streaming_does_not_buffer_all_records(tmp_path):
    pulled = []

    class CountingPlugin(FakePlugin):
        def read_records(self, path, range=None):
            recs = self._records[path]

            async def gen():
                for i, r in enumerate(recs):
                    pulled.append(i)
                    yield r

            return gen()

    recs = [{"number": i, "title": f"t{i}", "body": "x"} for i in range(5)]
    plugin = CountingPlugin(records={"/issues": recs})
    ctx = build_ctx(artifacts=FakeArtifactStore(tmp_path))

    agen = RecordCollectionProducer(ctx).produce(_task(plugin))
    first = await agen.__anext__()
    # after the first chunk only one record has been pulled — proves we stream, not buffer
    assert isinstance(first, Chunk)
    assert pulled == [0]
    await agen.aclose()


async def test_chunk_max_truncates_and_flags_partial(tmp_path):
    recs = [{"number": i, "title": f"t{i}", "body": "x"} for i in range(5)]
    plugin = FakePlugin(records={"/issues": recs})
    ctx = build_ctx(artifacts=FakeArtifactStore(tmp_path))
    ocfg = ObjectConfig(text_fields=["title"], locator_fields=["number"], chunk_max=2)
    items = await collect(RecordCollectionProducer(ctx), _task(plugin, ocfg))
    chunks = [x for x in items if isinstance(x, Chunk)]
    assert len(chunks) == 2
    assert isinstance(items[-1], EndOfTask) and items[-1].partial is True


async def test_head_cache_artifact_written(tmp_path):
    store = FakeArtifactStore(tmp_path)
    recs = [{"number": 1, "title": "a", "body": "b"}]
    plugin = FakePlugin(records={"/issues": recs})
    ctx = build_ctx(artifacts=store)
    await collect(RecordCollectionProducer(ctx), _task(plugin))
    head = await store.get_artifact("default", "gh://o/r/issues", "head_cache")
    assert head is not None and b'"number": 1' in head


async def test_field_missing_raises(tmp_path):
    # records exist but none carry any configured text_field key -> field_missing
    recs = [{"number": 1, "other": "x"}, {"number": 2, "other": "y"}]
    plugin = FakePlugin(records={"/issues": recs})
    ctx = build_ctx(artifacts=FakeArtifactStore(tmp_path))
    ocfg = ObjectConfig(text_fields=["title", "body"], locator_fields=["number"])
    with pytest.raises(ValueError, match="field_missing"):
        await collect(RecordCollectionProducer(ctx), _task(plugin, ocfg))


async def test_connector_partial_flag_propagates(tmp_path):
    recs = [{"number": 1, "title": "a", "body": "b"}]
    plugin = FakePlugin(records={"/issues": recs})
    plugin.ctx.declare_partial("/issues")  # connector capped its read
    ctx = build_ctx(artifacts=FakeArtifactStore(tmp_path))
    items = await collect(RecordCollectionProducer(ctx), _task(plugin))
    assert isinstance(items[-1], EndOfTask) and items[-1].partial is True


async def test_no_text_fields_yields_only_end_of_task(tmp_path):
    plugin = FakePlugin(records={"/issues": [{"number": 1}]})
    ctx = build_ctx(artifacts=FakeArtifactStore(tmp_path))
    items = await collect(RecordCollectionProducer(ctx), _task(plugin, ObjectConfig()))
    assert len(items) == 1 and isinstance(items[0], EndOfTask)
