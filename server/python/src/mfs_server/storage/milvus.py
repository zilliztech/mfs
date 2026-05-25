"""Milvus store (design/06 §1, design/02 §9.4/§10.3).

One schema shared by both collection_strategy values; the only fork is
resolve_collection(). partition_key = connector_uri. sparse_vec is produced by a
built-in BM25 Function from `content`, so writers only provide content + dense_vec.

pymilvus MilvusClient is synchronous; callers (worker/engine) wrap calls in
asyncio.to_thread. Works against Milvus Lite (local file) and Zilliz Cloud.
"""
from __future__ import annotations

from typing import Any, Optional

from pymilvus import DataType, Function, FunctionType, MilvusClient

from ..config import ServerConfig


class MilvusStore:
    def __init__(self, cfg: ServerConfig):
        self.uri = cfg.milvus.uri
        self.token = cfg.milvus.token
        self.strategy = cfg.milvus.collection_strategy
        self.num_partitions = cfg.milvus.num_partitions
        self.dim = cfg.embedding.dim
        self.client: Optional[MilvusClient] = None

    def connect(self) -> None:
        kwargs: dict[str, Any] = {"uri": self.uri}
        if self.token:
            kwargs["token"] = self.token
        self.client = MilvusClient(**kwargs)

    def is_lite(self) -> bool:
        return self.uri.endswith(".db") or self.uri.startswith("./") or "/" in self.uri and not self.uri.startswith("http")

    def resolve_collection(self, namespace_id: str) -> str:
        if self.strategy == "per_namespace":
            return f"mfs_chunks__{namespace_id}"
        return "mfs_chunks"

    def _build_schema(self):
        assert self.client is not None
        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("chunk_id", DataType.VARCHAR, max_length=128, is_primary=True)
        schema.add_field("namespace_id", DataType.VARCHAR, max_length=64)
        schema.add_field("connector_uri", DataType.VARCHAR, max_length=256, is_partition_key=True)
        schema.add_field("object_uri", DataType.VARCHAR, max_length=1024)
        schema.add_field("locator", DataType.JSON, nullable=True)
        schema.add_field("lines", DataType.JSON, nullable=True)
        schema.add_field("content", DataType.VARCHAR, max_length=65535, enable_analyzer=True)
        schema.add_field("dense_vec", DataType.FLOAT_VECTOR, dim=self.dim)
        schema.add_field("sparse_vec", DataType.SPARSE_FLOAT_VECTOR)
        schema.add_field("chunk_kind", DataType.VARCHAR, max_length=32)
        schema.add_field("metadata", DataType.JSON, nullable=True)
        schema.add_field("indexed_at", DataType.INT64)
        schema.add_function(
            Function(
                name="content_bm25",
                function_type=FunctionType.BM25,
                input_field_names=["content"],
                output_field_names=["sparse_vec"],
            )
        )
        return schema

    def _build_index_params(self):
        assert self.client is not None
        ip = MilvusClient.prepare_index_params()
        ip.add_index(field_name="dense_vec", index_type="HNSW", metric_type="COSINE",
                     params={"M": 16, "efConstruction": 200})
        ip.add_index(field_name="sparse_vec", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25")
        # NOTE: scalar INVERTED indexes (namespace_id/object_uri/chunk_kind, design/06 §1)
        # are a filter optimization, not functionally required — Milvus filters work without
        # them (full scan, fine at small scale). Milvus Lite 3.0 rejects scalar create_index
        # ("missing metric_type"), so we add them best-effort post-create (see add_scalar_indexes)
        # and tolerate failure. TODO(perf): revisit when Lite supports scalar indexes.
        return ip

    SCALAR_INDEX_FIELDS = ("namespace_id", "object_uri", "chunk_kind")

    def _add_scalar_indexes(self, name: str) -> None:
        assert self.client is not None
        for f in self.SCALAR_INDEX_FIELDS:
            try:
                ip = MilvusClient.prepare_index_params()
                ip.add_index(field_name=f, index_type="INVERTED")
                self.client.create_index(collection_name=name, index_params=ip)
            except Exception:
                pass  # Lite or backend without scalar-index support: filter falls back to scan

    def ensure_collection(self, namespace_id: str) -> str:
        assert self.client is not None
        name = self.resolve_collection(namespace_id)
        if self.client.has_collection(name):
            return name
        kwargs: dict[str, Any] = {
            "collection_name": name,
            "schema": self._build_schema(),
            "index_params": self._build_index_params(),
        }
        # num_partitions only meaningful with a partition_key field
        kwargs["num_partitions"] = self.num_partitions
        self.client.create_collection(**kwargs)
        self._add_scalar_indexes(name)
        self.client.load_collection(name)
        return name

    def drop_collection(self, namespace_id: str) -> None:
        assert self.client is not None
        name = self.resolve_collection(namespace_id)
        if self.client.has_collection(name):
            self.client.drop_collection(name)

    def upsert(self, namespace_id: str, rows: list[dict]) -> None:
        """rows must contain content + dense_vec (+ scalar fields); sparse_vec is
        auto-generated by the BM25 Function and must NOT be supplied."""
        assert self.client is not None
        if not rows:
            return
        name = self.resolve_collection(namespace_id)
        self.client.upsert(collection_name=name, data=rows)

    def delete_by_object(self, namespace_id: str, connector_uri: str, object_uri: str) -> None:
        assert self.client is not None
        name = self.resolve_collection(namespace_id)
        flt = (
            f'namespace_id == "{namespace_id}" and connector_uri == "{connector_uri}" '
            f'and object_uri == "{object_uri}"'
        )
        self.client.delete(collection_name=name, filter=flt)

    def delete_by_connector(self, namespace_id: str, connector_uri: str) -> None:
        assert self.client is not None
        name = self.resolve_collection(namespace_id)
        flt = f'namespace_id == "{namespace_id}" and connector_uri == "{connector_uri}"'
        self.client.delete(collection_name=name, filter=flt)

    def count(self, namespace_id: str, expr: str = "") -> int:
        assert self.client is not None
        name = self.resolve_collection(namespace_id)
        rows = self.client.query(collection_name=name, filter=expr or "chunk_id != ''",
                                 output_fields=["count(*)"])
        if rows and "count(*)" in rows[0]:
            return int(rows[0]["count(*)"])
        return 0

    def search_dense(self, namespace_id: str, query_vec: list[float], limit: int,
                     expr: str = "", output_fields: Optional[list[str]] = None,
                     consistency_level: str = "Strong") -> list[dict]:
        assert self.client is not None
        name = self.resolve_collection(namespace_id)
        res = self.client.search(
            collection_name=name,
            data=[query_vec],
            anns_field="dense_vec",
            limit=limit,
            filter=expr,
            output_fields=output_fields or ["chunk_id", "object_uri", "content", "chunk_kind", "locator", "lines", "metadata"],
            search_params={"metric_type": "COSINE"},
            consistency_level=consistency_level,
        )
        return list(res[0]) if res else []
