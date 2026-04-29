"""Milvus store tests (uses Milvus Lite in a temp directory)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mfs.config import MilvusConfig
from mfs.store import ChunkRecord, MilvusStore


def _record(chunk_id: str, source: str, text: str, dim: int = 8) -> ChunkRecord:
    return ChunkRecord(
        id=chunk_id,
        source=source,
        parent_dir=str(Path(source).parent),
        chunk_index=0,
        start_line=1,
        end_line=3,
        chunk_text=text,
        dense_vector=[0.1] * dim,
        content_type="markdown",
        file_hash="abc",
        is_dir=False,
        embed_status="complete",
        metadata={"heading_text": "x"},
        account_id="default",
    )


@pytest.fixture
def store(tmp_path):
    cfg = MilvusConfig(uri=str(tmp_path / "milvus.db"))
    s = MilvusStore(cfg, dimension=8)
    s.connect()
    return s


def test_insert_and_query(store):
    store.insert_chunks([
        _record("id1", "/tmp/foo.md", "authentication and oauth2 tokens"),
        _record("id2", "/tmp/bar.md", "deployment configuration"),
    ])
    indexed = store.get_indexed_files("/tmp")
    assert set(indexed.keys()) == {"/tmp/foo.md", "/tmp/bar.md"}
    counts = store.count_all()
    assert counts["files"] == 2


def test_keyword_search(store):
    store.insert_chunks([
        _record("id1", "/tmp/foo.md", "authentication flow with OAuth2 tokens"),
        _record("id2", "/tmp/bar.md", "deployment configuration for kubernetes"),
    ])
    results = store.keyword_search("authentication", "/tmp", top_k=5)
    assert any(r.source == "/tmp/foo.md" for r in results)


def test_delete_by_sources(store):
    store.insert_chunks([
        _record("id1", "/tmp/foo.md", "hello"),
        _record("id2", "/tmp/bar.md", "world"),
    ])
    store.delete_by_sources(["/tmp/foo.md"])
    remaining = store.get_indexed_files("/tmp")
    assert "/tmp/foo.md" not in remaining
    assert "/tmp/bar.md" in remaining
