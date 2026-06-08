"""Unit tests for TextChunksProducer — code, markdown rules, converter, truncation."""

from __future__ import annotations

from mfs_server.connectors.base import ObjectConfig
from mfs_server.engine.producers import Chunk, EndOfTask, ObjectTask, TextChunksProducer

from _fakes import FakeArtifactStore, FakePlugin, build_ctx, collect

_MD = (
    "# Heading One\n\n"
    "Some intro paragraph here that is reasonably long to exercise the splitter.\n\n"
    "## Heading Two\n\n"
    "Another paragraph under heading two with more words to fill the budget.\n\n"
    "### Sub heading\n\n"
    "Final bit of text goes here under the third heading."
)


def _task(uri, connector_uri, okind, plugin, ocfg=None):
    return ObjectTask(
        object_uri=uri,
        connector_uri=connector_uri,
        okind=okind,
        connector_job_id="job1",
        plugin=plugin,
        ocfg=ocfg,
    )


async def test_code_chunks_have_line_locators(tmp_path):
    src = "def foo():\n    return 1\n\n\ndef bar():\n    return 2\n"
    plugin = FakePlugin(data={"/m.py": src.encode()})
    ctx = build_ctx(artifacts=FakeArtifactStore(tmp_path))
    items = await collect(TextChunksProducer(ctx), _task("/m.py", "file:///r", "code", plugin))

    chunks = [x for x in items if isinstance(x, Chunk)]
    assert chunks and all(c.chunk_kind == "body" for c in chunks)
    for c in chunks:
        assert "lines" in c.locator
        s, e = c.locator["lines"]
        assert 1 <= s <= e  # 1-based, ascending
        assert c.uri == "file:///r/m.py" and c.connector_job_id == "job1"
    assert isinstance(items[-1], EndOfTask) and items[-1].partial is False


async def test_markdown_rules_split_on_heading(tmp_path):
    plugin = FakePlugin(data={"/doc.md": _MD.encode()})
    ctx = build_ctx(artifacts=FakeArtifactStore(tmp_path), chunk_size=64)
    items = await collect(
        TextChunksProducer(ctx), _task("/doc.md", "file:///r", "document", plugin)
    )
    chunks = [x for x in items if isinstance(x, Chunk)]

    # heading-first rules keep each heading at a chunk boundary rather than buried
    assert len(chunks) >= 3
    starts = [c.content.lstrip() for c in chunks]
    assert any(s.startswith("Heading Two") or s.startswith("## Heading Two") for s in starts)
    assert any(s.startswith("Sub heading") or s.startswith("### Sub heading") for s in starts)
    # no chunk swallows two headings
    for c in chunks:
        assert c.content.count("Heading Two") + c.content.count("Sub heading") <= 1


async def test_chunk_max_truncates_and_flags_partial(tmp_path):
    plugin = FakePlugin(data={"/doc.md": _MD.encode()})
    ctx = build_ctx(artifacts=FakeArtifactStore(tmp_path), chunk_size=64)
    ocfg = ObjectConfig(chunk_max=1)
    items = await collect(
        TextChunksProducer(ctx), _task("/doc.md", "file:///r", "document", plugin, ocfg)
    )
    chunks = [x for x in items if isinstance(x, Chunk)]
    assert len(chunks) == 1
    assert isinstance(items[-1], EndOfTask) and items[-1].partial is True


async def test_document_convert_ext_uses_converter_and_caches_artifact(tmp_path):
    store = FakeArtifactStore(tmp_path)
    plugin = FakePlugin(data={"/report.pdf": b"raw pdf bytes here"})
    ctx = build_ctx(artifacts=store)
    items = await collect(
        TextChunksProducer(ctx), _task("/report.pdf", "file:///r", "document", plugin)
    )
    chunks = [x for x in items if isinstance(x, Chunk)]
    assert ctx.converter.calls == 1
    assert chunks  # converted markdown produced at least one chunk
    # converted_md persisted for `mfs cat`
    md = await store.get_artifact("default", "file:///r/report.pdf", "converted_md")
    assert md is not None and md.decode().startswith("# Converted .pdf")


async def test_web_text_persists_converted_md(tmp_path):
    store = FakeArtifactStore(tmp_path)
    plugin = FakePlugin(data={"/page": b"# Title\n\nbody text"})
    ctx = build_ctx(artifacts=store)
    await collect(TextChunksProducer(ctx), _task("/page", "web://site", "document", plugin))
    md = await store.get_artifact("default", "web://site/page", "converted_md")
    assert md is not None and md.decode() == "# Title\n\nbody text"


async def test_empty_document_yields_only_end_of_task(tmp_path):
    plugin = FakePlugin(data={"/empty.md": b"   \n  "})
    ctx = build_ctx(artifacts=FakeArtifactStore(tmp_path))
    items = await collect(
        TextChunksProducer(ctx), _task("/empty.md", "file:///r", "document", plugin)
    )
    assert len(items) == 1 and isinstance(items[0], EndOfTask)
