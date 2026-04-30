"""Milvus storage layer: schema management, CRUD, search operations.

Single collection, single embedding model. Milvus Lite by default.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pymilvus import (
    AnnSearchRequest,
    CollectionSchema,
    DataType,
    FieldSchema,
    Function,
    FunctionType,
    MilvusClient,
    RRFRanker,
)

from . import constants as C
from .config import MilvusConfig


@dataclass
class ChunkRecord:
    id: str
    source: str
    parent_dir: str
    chunk_index: int
    start_line: int
    end_line: int
    chunk_text: str
    dense_vector: list[float] | None
    content_type: str
    file_hash: str
    is_dir: bool
    embed_status: str  # "pending" | "complete"
    metadata: dict | None
    account_id: str


@dataclass
class SearchResult:
    source: str
    chunk_text: str
    chunk_index: int
    start_line: int
    end_line: int
    content_type: str
    score: float
    is_dir: bool
    metadata: dict | None


# Placeholder dense vector used for pending rows: a zero vector is inserted at
# index time so the row is searchable (with score ~0 on dense), and overwritten
# once the worker finishes embedding. For MVP we only insert complete rows.
def _zero_vector(dim: int) -> list[float]:
    return [0.0] * dim


class MilvusStore:
    """Milvus collection management and operations."""

    def __init__(self, config: MilvusConfig, dimension: int):
        self._config = config
        self._dimension = dimension
        self._client: MilvusClient | None = None

    # ------------------------------------------------------------------ setup

    def connect(self) -> None:
        if self._client is not None:
            return
        uri = self._config.uri
        # Milvus Lite takes a local file path as URI.
        if not uri.startswith(("http://", "https://", "tcp://", "unix://")):
            Path(uri).parent.mkdir(parents=True, exist_ok=True)
        kwargs: dict[str, Any] = {"uri": uri}
        if self._config.token:
            kwargs["token"] = self._config.token
        self._client = MilvusClient(**kwargs)
        self.ensure_collection()

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    @property
    def client(self) -> MilvusClient:
        if self._client is None:
            raise RuntimeError("MilvusStore.connect() has not been called")
        return self._client

    def ensure_collection(self) -> None:
        """Create the collection if missing; validate dimension if it exists."""
        name = self._config.collection_name
        if self.client.has_collection(name):
            info = self.client.describe_collection(name)
            for f in info.get("fields", []):
                if f.get("name") == "dense_vector":
                    params = f.get("params") or {}
                    existing = int(params.get("dim", params.get("dimension", 0)) or 0)
                    if existing and existing != self._dimension:
                        raise RuntimeError(
                            "Embedding model dimension mismatch: existing collection "
                            f"has dim={existing}, config requests dim={self._dimension}. "
                            "Drop the collection manually to rebuild from scratch."
                        )
            return

        fields = [
            FieldSchema("id", DataType.VARCHAR, max_length=32, is_primary=True),
            FieldSchema("source", DataType.VARCHAR, max_length=C.MAX_SOURCE_LEN),
            FieldSchema("parent_dir", DataType.VARCHAR, max_length=C.MAX_SOURCE_LEN),
            FieldSchema("chunk_index", DataType.INT16),
            FieldSchema("start_line", DataType.INT32),
            FieldSchema("end_line", DataType.INT32),
            FieldSchema(
                "chunk_text",
                DataType.VARCHAR,
                max_length=C.MAX_CHUNK_TEXT_LEN,
                enable_analyzer=True,
            ),
            FieldSchema("dense_vector", DataType.FLOAT_VECTOR, dim=self._dimension),
            FieldSchema("sparse_vector", DataType.SPARSE_FLOAT_VECTOR),
            FieldSchema("content_type", DataType.VARCHAR, max_length=32),
            FieldSchema("file_hash", DataType.VARCHAR, max_length=64),
            FieldSchema("is_dir", DataType.BOOL),
            FieldSchema("embed_status", DataType.VARCHAR, max_length=16),
            FieldSchema("metadata", DataType.JSON),
            FieldSchema("account_id", DataType.VARCHAR, max_length=64),
        ]
        schema = CollectionSchema(fields=fields)

        # Server-side BM25: chunk_text → sparse_vector
        schema.add_function(
            Function(
                name="bm25",
                function_type=FunctionType.BM25,
                input_field_names=["chunk_text"],
                output_field_names=["sparse_vector"],
            )
        )

        index_params = self.client.prepare_index_params()
        index_params.add_index(
            field_name="dense_vector", index_type="AUTOINDEX", metric_type="COSINE"
        )
        index_params.add_index(
            field_name="sparse_vector",
            index_type="SPARSE_INVERTED_INDEX",
            metric_type="BM25",
        )
        index_params.add_index(field_name="source", index_type="INVERTED")
        index_params.add_index(field_name="parent_dir", index_type="INVERTED")
        index_params.add_index(field_name="content_type", index_type="INVERTED")
        index_params.add_index(field_name="is_dir", index_type="INVERTED")

        self.client.create_collection(
            collection_name=name,
            schema=schema,
            index_params=index_params,
        )

    # --------------------------------------------------------------- writes

    def insert_chunks(self, records: list[ChunkRecord]) -> None:
        if not records:
            return
        rows: list[dict[str, Any]] = []
        for r in records:
            rows.append(
                {
                    "id": r.id,
                    "source": r.source[: C.MAX_SOURCE_LEN],
                    "parent_dir": r.parent_dir[: C.MAX_SOURCE_LEN],
                    "chunk_index": int(r.chunk_index),
                    "start_line": int(r.start_line),
                    "end_line": int(r.end_line),
                    "chunk_text": r.chunk_text[: C.MAX_CHUNK_TEXT_LEN],
                    "dense_vector": r.dense_vector
                    if r.dense_vector is not None
                    else _zero_vector(self._dimension),
                    "content_type": r.content_type,
                    "file_hash": r.file_hash,
                    "is_dir": bool(r.is_dir),
                    "embed_status": r.embed_status,
                    "metadata": r.metadata or {},
                    "account_id": r.account_id,
                }
            )
        self.client.upsert(collection_name=self._config.collection_name, data=rows)

    def _query_all(
        self,
        filter_expr: str,
        output_fields: list[str],
        batch_size: int = 1000,
    ) -> list[dict]:
        """Iterate-and-collect all rows matching ``filter_expr``.

        Replaces the hard-coded ``limit=16384`` pattern. Uses Milvus
        ``query_iterator`` when available; falls back to id-cursor
        pagination via plain ``query`` otherwise. An empty ``filter_expr``
        is accepted (full-collection scan).
        """
        self.connect()
        collection = self._config.collection_name
        rows: list[dict] = []

        iterator = None
        if hasattr(self._client, "query_iterator"):
            try:
                iterator = self._client.query_iterator(
                    collection_name=collection,
                    filter=filter_expr,
                    output_fields=output_fields,
                    batch_size=batch_size,
                )
            except Exception:
                iterator = None

        if iterator is not None:
            try:
                while True:
                    batch = iterator.next()
                    if not batch:
                        break
                    rows.extend(batch)
            finally:
                try:
                    iterator.close()
                except Exception:
                    pass
            return rows

        # Fallback: manual id-cursor pagination for older pymilvus versions.
        fields_with_id = list(output_fields)
        if "id" not in fields_with_id:
            fields_with_id.append("id")
        last_id = ""
        while True:
            cursor_expr = f'id > "{_escape(last_id)}"'
            combined = f"({filter_expr}) and {cursor_expr}" if filter_expr else cursor_expr
            batch = self._client.query(
                collection_name=collection,
                filter=combined,
                output_fields=fields_with_id,
                limit=batch_size,
            )
            if not batch:
                break
            rows.extend(batch)
            last_id = max(r["id"] for r in batch)
            if len(batch) < batch_size:
                break
        return rows

    def _count_matching(self, expr: str) -> int:
        try:
            rows = self._query_all(expr, output_fields=["id"])
        except Exception:
            return 0
        return len(rows)

    def _delete(self, expr: str) -> int:
        """Delete rows matching `expr` and return the count deleted."""
        count = self._count_matching(expr)
        if count == 0:
            return 0
        res = self.client.delete(
            collection_name=self._config.collection_name, filter=expr
        )
        reported = _delete_count(res)
        return reported if reported else count

    def delete_by_source(self, source: str) -> int:
        return self._delete(f'source == "{_escape(source)}"')

    def delete_body_chunks_by_source(self, source: str) -> int:
        """Delete body chunks (chunk_index != -1) for a file; preserve LLM summary."""
        return self._delete(f'source == "{_escape(source)}" and chunk_index != -1')

    def delete_by_sources(self, sources: list[str]) -> int:
        if not sources:
            return 0
        escaped = ", ".join(f'"{_escape(s)}"' for s in sources)
        return self._delete(f"source in [{escaped}]")

    def delete_by_ids(self, ids: list[str]) -> int:
        if not ids:
            return 0
        escaped = ", ".join(f'"{_escape(i)}"' for i in ids)
        return self._delete(f"id in [{escaped}]")

    def get_body_chunk_ids(self, source: str) -> set[str]:
        """Return body chunk ids for a single source, excluding summaries."""
        rows = self._query_all(
            f'source == "{_escape(source)}" and is_dir == false and chunk_index != -1',
            output_fields=["id"],
        )
        return {str(r["id"]) for r in rows if r.get("id")}

    def update_file_hash_by_ids(self, ids: list[str], file_hash: str) -> int:
        """Update ``file_hash`` for existing chunks without re-embedding them."""
        if not ids:
            return 0
        escaped = ", ".join(f'"{_escape(i)}"' for i in ids)
        rows = self._query_all(
            f"id in [{escaped}]",
            output_fields=[
                "id", "source", "parent_dir", "chunk_index", "start_line", "end_line",
                "chunk_text", "dense_vector", "content_type", "file_hash",
                "is_dir", "embed_status", "metadata", "account_id",
            ],
        )
        for row in rows:
            row["file_hash"] = file_hash
        if rows:
            self.client.upsert(collection_name=self._config.collection_name, data=rows)
        return len(rows)

    def delete_by_prefix(self, path_prefix: str) -> int:
        """Delete all chunks whose source starts with `path_prefix`.

        Milvus ``like`` treats ``%`` / ``_`` as wildcards and has no escape
        syntax, so for prefixes containing those characters we over-match
        on the server then restrict to exact-prefix matches in Python
        before issuing the delete by id.
        """
        if not _has_like_wildcards(path_prefix):
            return self._delete(f'source like "{_escape(path_prefix)}%"')
        rows = self._query_all(
            f'source like "{_escape(path_prefix)}%"',
            output_fields=["id", "source"],
        )
        matching_ids = [r["id"] for r in rows if str(r.get("source", "")).startswith(path_prefix)]
        return self.delete_by_ids(matching_ids)

    def delete_dir_record(self, dir_path: str) -> int:
        """Delete the `is_dir=true` summary record for `dir_path`."""
        return self._delete(f'source == "{_escape(dir_path)}" and is_dir == true')

    def mark_summary_stale(self, source: str) -> int:
        """Mark the LLM summary chunk for `source` as stale via metadata."""
        expr = f'source == "{_escape(source)}" and chunk_index == -1'
        rows = self._query_all(expr, output_fields=["id", "metadata"])
        if not rows:
            return 0
        updates: list[dict] = []
        for r in rows:
            meta = r.get("metadata") or {}
            if isinstance(meta, str):
                meta = {}
            meta["stale"] = True
            updates.append({"id": r["id"], "metadata": meta})
        # We must re-upsert full rows. Fetch them first to preserve all fields.
        id_list = ", ".join(chr(34) + _escape(u["id"]) + chr(34) for u in updates)
        full = self._query_all(
            f"id in [{id_list}]",
            output_fields=[
                "id", "source", "parent_dir", "chunk_index", "start_line", "end_line",
                "chunk_text", "dense_vector", "content_type", "file_hash",
                "is_dir", "embed_status", "metadata", "account_id",
            ],
        )
        for row in full:
            row_meta = row.get("metadata") or {}
            if isinstance(row_meta, str):
                row_meta = {}
            row_meta["stale"] = True
            row["metadata"] = row_meta
        if full:
            self.client.upsert(collection_name=self._config.collection_name, data=full)
        return len(full)

    # --------------------------------------------------------------- reads

    def get_indexed_files(self, path_prefix: str) -> dict[str, str]:
        """Return {source: file_hash} for body chunks under `path_prefix`.

        Excludes directory records (is_dir=True) and LLM summary chunks
        (chunk_index=-1) so the caller sees only files with indexed content.
        """
        expr = (
            f'source like "{_escape(path_prefix)}%" and is_dir == false '
            f'and chunk_index != -1'
        )
        rows = self._query_all(expr, output_fields=["source", "file_hash"])
        needs_filter = _has_like_wildcards(path_prefix)
        out: dict[str, str] = {}
        for r in rows:
            src = r["source"]
            if needs_filter and not src.startswith(path_prefix):
                continue
            out[src] = r["file_hash"]
        return out

    def list_dir_children(self, parent_dir: str) -> list[SearchResult]:
        """Return entries whose parent_dir == parent_dir (files + subdirs)."""
        expr = f'parent_dir == "{_escape(parent_dir)}"'
        rows = self._query_all(
            expr,
            output_fields=[
                "source", "chunk_text", "chunk_index", "start_line", "end_line",
                "content_type", "is_dir", "metadata",
            ],
        )
        results: list[SearchResult] = []
        for r in rows:
            metadata = r.get("metadata") or {}
            if isinstance(metadata, str):
                metadata = {}
            results.append(
                SearchResult(
                    source=r.get("source", "") or "",
                    chunk_text=r.get("chunk_text", "") or "",
                    chunk_index=int(r.get("chunk_index", 0) or 0),
                    start_line=int(r.get("start_line", 0) or 0),
                    end_line=int(r.get("end_line", 0) or 0),
                    content_type=r.get("content_type", "") or "",
                    score=0.0,
                    is_dir=bool(r.get("is_dir", False)),
                    metadata=metadata,
                )
            )
        return results

    def get_dir_summary(self, dir_path: str) -> SearchResult | None:
        expr = f'source == "{_escape(dir_path)}" and is_dir == true'
        rows = self.client.query(
            collection_name=self._config.collection_name,
            filter=expr,
            output_fields=[
                "source", "chunk_text", "chunk_index", "start_line", "end_line",
                "content_type", "is_dir", "metadata",
            ],
            limit=1,
        )
        if not rows:
            return None
        r = rows[0]
        metadata = r.get("metadata") or {}
        if isinstance(metadata, str):
            metadata = {}
        return SearchResult(
            source=r.get("source", "") or "",
            chunk_text=r.get("chunk_text", "") or "",
            chunk_index=int(r.get("chunk_index", 0) or 0),
            start_line=int(r.get("start_line", 0) or 0),
            end_line=int(r.get("end_line", 0) or 0),
            content_type=r.get("content_type", "") or "",
            score=0.0,
            is_dir=True,
            metadata=metadata,
        )

    def get_llm_summaries(self, path_prefix: str) -> dict[str, dict]:
        """Return {source: metadata} for LLM summary chunks under `path_prefix`."""
        expr = f'source like "{_escape(path_prefix)}%" and chunk_index == -1'
        rows = self._query_all(
            expr, output_fields=["source", "chunk_text", "content_type", "metadata"],
        )
        needs_filter = _has_like_wildcards(path_prefix)
        out: dict[str, dict] = {}
        for r in rows:
            src = r["source"]
            if needs_filter and not src.startswith(path_prefix):
                continue
            meta = r.get("metadata") or {}
            if isinstance(meta, str):
                meta = {}
            meta["text"] = r.get("chunk_text", "") or ""
            meta["content_type"] = r.get("content_type", "llm_summary")
            out[src] = meta
        return out

    def count_under(self, path_prefix: str) -> dict[str, int]:
        """Return counts by embed_status under `path_prefix`."""
        expr = f'source like "{_escape(path_prefix)}%"'
        rows = self._query_all(
            expr, output_fields=["embed_status", "source", "is_dir"],
        )
        if _has_like_wildcards(path_prefix):
            rows = [r for r in rows if str(r.get("source", "")).startswith(path_prefix)]
        file_rows = [r for r in rows if not r.get("is_dir", False)]
        dir_rows = [r for r in rows if r.get("is_dir", False)]
        complete = sum(1 for r in file_rows if r.get("embed_status") == "complete")
        pending = sum(1 for r in file_rows if r.get("embed_status") == "pending")
        sources = {r["source"] for r in file_rows}
        return {
            "total_chunks": len(file_rows),
            "complete_chunks": complete,
            "pending_chunks": pending,
            "files": len(sources),
            "dir_summaries": len(dir_rows),
        }

    def is_empty(self) -> bool:
        """Return True iff the collection currently holds zero rows.

        Used as a preflight before search so that hybrid / BM25 kernels don't
        assert on an empty sparse inverted index (the Milvus C++ BM25 ranker
        produces NaN IDF values when there are no documents to score).
        """
        try:
            rows = self.client.query(
                collection_name=self._config.collection_name,
                filter="",
                output_fields=["id"],
                limit=1,
            )
        except Exception:
            return True
        return not rows

    def count_all(self) -> dict[str, int]:
        rows = self._query_all(
            "", output_fields=["embed_status", "source", "is_dir"],
        )
        # Directory summaries are bookkeeping rows the user never asked for —
        # surface them under a separate key so "Indexed files" means what it
        # says. Body-chunk counts exclude them too, so Chunks ≈ what got
        # embedded from the user's files.
        file_rows = [r for r in rows if not r.get("is_dir", False)]
        dir_rows = [r for r in rows if r.get("is_dir", False)]
        complete = sum(1 for r in file_rows if r.get("embed_status") == "complete")
        pending = sum(1 for r in file_rows if r.get("embed_status") == "pending")
        sources = {r["source"] for r in file_rows}
        return {
            "total_chunks": len(file_rows),
            "complete_chunks": complete,
            "pending_chunks": pending,
            "files": len(sources),
            "dir_summaries": len(dir_rows),
        }

    # --------------------------------------------------------------- search

    def hybrid_search(
        self,
        query_vector: list[float],
        query_text: str,
        path_filter: str | None,
        top_k: int = 10,
    ) -> list[SearchResult]:
        filter_expr = self._make_filter(path_filter, include_dirs=True)
        dense_req = AnnSearchRequest(
            data=[query_vector],
            anns_field="dense_vector",
            param={"metric_type": "COSINE", "params": {}},
            limit=top_k * 2,
            expr=filter_expr,
        )
        sparse_req = AnnSearchRequest(
            data=[query_text],
            anns_field="sparse_vector",
            param={"metric_type": "BM25", "params": {}},
            limit=top_k * 2,
            expr=filter_expr,
        )
        hits = self.client.hybrid_search(
            collection_name=self._config.collection_name,
            reqs=[dense_req, sparse_req],
            ranker=RRFRanker(k=60),
            limit=top_k,
            output_fields=[
                "source", "chunk_text", "chunk_index", "start_line", "end_line",
                "content_type", "is_dir", "metadata",
            ],
        )
        return _to_results(hits)

    def semantic_search(
        self,
        query_vector: list[float],
        path_filter: str | None,
        top_k: int = 10,
    ) -> list[SearchResult]:
        filter_expr = self._make_filter(path_filter, include_dirs=True)
        hits = self.client.search(
            collection_name=self._config.collection_name,
            data=[query_vector],
            anns_field="dense_vector",
            search_params={"metric_type": "COSINE", "params": {}},
            limit=top_k,
            filter=filter_expr,
            output_fields=[
                "source", "chunk_text", "chunk_index", "start_line", "end_line",
                "content_type", "is_dir", "metadata",
            ],
        )
        return _to_results(hits)

    def keyword_search(
        self,
        query_text: str,
        path_filter: str | None,
        top_k: int = 10,
    ) -> list[SearchResult]:
        filter_expr = self._make_filter(path_filter, include_dirs=True)
        hits = self.client.search(
            collection_name=self._config.collection_name,
            data=[query_text],
            anns_field="sparse_vector",
            search_params={"metric_type": "BM25", "params": {}},
            limit=top_k,
            filter=filter_expr,
            output_fields=[
                "source", "chunk_text", "chunk_index", "start_line", "end_line",
                "content_type", "is_dir", "metadata",
            ],
        )
        return _to_results(hits)

    def bm25_prefilter(
        self,
        query_text: str,
        path_filter: str | None,
        top_k: int = 100,
    ) -> list[str]:
        """Return deduped source paths from BM25 hits (used as grep prefilter)."""
        results = self.keyword_search(query_text, path_filter, top_k=top_k)
        seen: set[str] = set()
        out: list[str] = []
        for r in results:
            if r.is_dir:
                continue
            if r.source in seen:
                continue
            seen.add(r.source)
            out.append(r.source)
        return out

    # --------------------------------------------------------------- helpers

    def _make_filter(self, path_filter: str | None, include_dirs: bool) -> str:
        parts: list[str] = []
        if path_filter:
            # Milvus LIKE lacks wildcard escaping; for path_filters containing
            # `%` or `_` the server-side filter over-matches. That's acceptable
            # here because the search result cap is small and callers that need
            # exact semantics can re-filter by source in Python. We still escape
            # `"`/`\` via _escape to block filter injection.
            parts.append(f'source like "{_escape(path_filter)}%"')
        if not include_dirs:
            parts.append("is_dir == false")
        return " and ".join(parts)


def _escape(s: str) -> str:
    """Escape for Milvus filter string literal (equality/membership use).

    Order matters: backslash must be escaped first, otherwise we'd double-
    escape the slashes produced by the second replace.
    """
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _has_like_wildcards(s: str) -> bool:
    """True if *s* contains LIKE wildcard characters.

    Milvus's ``like`` operator treats ``%`` and ``_`` as wildcards and does
    not accept a backslash-escape syntax for them. Callers that build LIKE
    predicates from user paths must therefore post-filter results in Python
    via ``str.startswith`` / equality to avoid over-matching (e.g. a prefix
    ``/tmp/foo_bar`` otherwise also matches ``/tmp/fooXbar``).
    """
    return "%" in s or "_" in s


def _delete_count(res: Any) -> int:
    if res is None:
        return 0
    if isinstance(res, dict):
        return int(res.get("delete_count", 0) or 0)
    return int(getattr(res, "delete_count", 0) or 0)


def _to_results(hits: Any) -> list[SearchResult]:
    """Flatten Milvus hits into SearchResult list.

    `hits` from hybrid_search/search is a list of lists (one per query); we have
    a single query so we consume the first entry.
    """
    if not hits:
        return []
    row = hits[0]
    results: list[SearchResult] = []
    for h in row:
        entity = h.get("entity", {}) if isinstance(h, dict) else getattr(h, "entity", {})
        if isinstance(entity, dict):
            get = entity.get
        else:
            get = lambda k, default=None: getattr(entity, k, default)  # noqa: E731
        score = float(h.get("distance", 0.0) if isinstance(h, dict) else getattr(h, "distance", 0.0))
        metadata = get("metadata", {}) or {}
        if isinstance(metadata, str):
            metadata = {}
        results.append(
            SearchResult(
                source=get("source", "") or "",
                chunk_text=get("chunk_text", "") or "",
                chunk_index=int(get("chunk_index", 0) or 0),
                start_line=int(get("start_line", 0) or 0),
                end_line=int(get("end_line", 0) or 0),
                content_type=get("content_type", "") or "",
                score=score,
                is_dir=bool(get("is_dir", False)),
                metadata=metadata,
            )
        )
    return results
