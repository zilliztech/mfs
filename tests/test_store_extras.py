"""Store tests for Stage 3/4 additions (dir records, LLM summaries, prefix deletion)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mfs.config import MilvusConfig
from mfs.store import ChunkRecord, MilvusStore


def _body(chunk_id: str, source: str, text: str = "hello") -> ChunkRecord:
    return ChunkRecord(
        id=chunk_id,
        source=source,
        parent_dir=str(Path(source).parent),
        chunk_index=0,
        start_line=1,
        end_line=3,
        chunk_text=text,
        dense_vector=[0.1] * 8,
        content_type="markdown",
        file_hash="h",
        is_dir=False,
        embed_status="complete",
        metadata={},
        account_id="default",
    )


def _dir(dir_path: str, text: str = "dir summary") -> ChunkRecord:
    return ChunkRecord(
        id="d" + dir_path.replace("/", "_")[:14],
        source=dir_path,
        parent_dir=str(Path(dir_path).parent),
        chunk_index=0,
        start_line=0,
        end_line=0,
        chunk_text=text,
        dense_vector=[0.0] * 8,
        content_type="directory",
        file_hash="",
        is_dir=True,
        embed_status="complete",
        metadata={"file_count": 3, "indexed_count": 2},
        account_id="default",
    )


def _llm(chunk_id: str, source: str, text: str) -> ChunkRecord:
    return ChunkRecord(
        id=chunk_id,
        source=source,
        parent_dir=str(Path(source).parent),
        chunk_index=-1,
        start_line=0,
        end_line=0,
        chunk_text=text,
        dense_vector=[0.2] * 8,
        content_type="llm_summary",
        file_hash="h",
        is_dir=False,
        embed_status="complete",
        metadata={"stale": False},
        account_id="default",
    )


@pytest.fixture
def store(tmp_path):
    cfg = MilvusConfig(uri=str(tmp_path / "milvus.db"))
    s = MilvusStore(cfg, dimension=8)
    s.connect()
    return s


def test_list_dir_children(store):
    store.insert_chunks([
        _body("a1", "/proj/a.md"),
        _body("b1", "/proj/b.md"),
        _body("c1", "/other/c.md"),
    ])
    entries = store.list_dir_children("/proj")
    sources = {e.source for e in entries}
    assert sources == {"/proj/a.md", "/proj/b.md"}


def test_get_dir_summary(store):
    store.insert_chunks([_dir("/proj", "the proj dir")])
    ds = store.get_dir_summary("/proj")
    assert ds is not None
    assert ds.is_dir is True
    assert "the proj dir" in ds.chunk_text


def test_get_llm_summaries(store):
    store.insert_chunks([
        _llm("s1", "/proj/a.md", "summary of a"),
        _body("a1", "/proj/a.md"),
    ])
    out = store.get_llm_summaries("/proj")
    assert "/proj/a.md" in out
    assert out["/proj/a.md"]["text"] == "summary of a"


def test_delete_by_prefix(store):
    store.insert_chunks([
        _body("a1", "/proj/a.md"),
        _body("b1", "/proj/nested/b.md"),
        _body("c1", "/other/c.md"),
    ])
    n = store.delete_by_prefix("/proj/")
    assert n == 2
    remaining = store.get_indexed_files("/")
    assert "/proj/a.md" not in remaining
    assert "/other/c.md" in remaining


def test_delete_body_chunks_preserves_summary(store):
    store.insert_chunks([
        _body("a1", "/proj/a.md"),
        _llm("s1", "/proj/a.md", "summary"),
    ])
    store.delete_body_chunks_by_source("/proj/a.md")
    llm = store.get_llm_summaries("/proj")
    assert "/proj/a.md" in llm
    # body is gone
    assert "/proj/a.md" not in store.get_indexed_files("/proj")


def test_mark_summary_stale(store):
    store.insert_chunks([
        _llm("s1", "/proj/a.md", "summary"),
    ])
    n = store.mark_summary_stale("/proj/a.md")
    assert n == 1
    llm = store.get_llm_summaries("/proj")
    assert llm["/proj/a.md"].get("stale") is True
