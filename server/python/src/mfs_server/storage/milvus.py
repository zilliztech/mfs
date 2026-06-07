"""Milvus store.

One schema shared by both collection_strategy values; the only fork is
resolve_collection(). partition_key = connector_uri. sparse_vec is produced by a
built-in BM25 Function from `content`, so writers only provide content + dense_vec.

pymilvus MilvusClient is synchronous; callers (worker/engine) wrap calls in
asyncio.to_thread. Works against Milvus Lite (local file) and Zilliz Cloud.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any, Optional
from urllib.parse import urlparse

from pymilvus import AnnSearchRequest, DataType, Function, FunctionType, MilvusClient, RRFRanker
from pymilvus.exceptions import MilvusException

from ..config import ServerConfig

# Bump whenever _build_schema changes in a way the previous layout can't serve (new/renamed
# field, changed BM25 function, etc.). It is baked into the collection name together with the
# embedding dim, so a build always targets a collection built for ITS schema/model and never
# silently reuses an incompatible one written by a different version (migrations out of scope).
_COLLECTION_SCHEMA_VERSION = 1

# Milvus rejects any search whose `offset + limit` exceeds this window with
# `MilvusException(code=65535, "invalid max query result window")`. A user-supplied
# top_k is validated against this before we touch Milvus so the limit surfaces as a
# clean envelope instead of a leaked 500. Hybrid search over-fetches (limit * ratio),
# so the effective window can exceed top_k — callers must account for that.
MILVUS_MAX_RESULT_WINDOW = 16384


def _is_analyzer_config_error(text: str) -> bool:
    """True when a create_collection failure is really an analyzer/tokenizer misconfig
    (unknown tokenizer type, or jieba configured but its package isn't installed) rather
    than a transient/backend error — so the caller can fail clean instead of crash-looping."""
    t = text.lower()
    return any(k in t for k in ("tokenizer", "analyzer", "jieba"))


def _reraise_known_milvus_error(exc: MilvusException) -> None:
    """Translate Milvus errors that are really user/config problems into stable domain
    errors (raised as ValueError) so the API renders a clean 4xx envelope instead of leaking
    a raw MilvusException as a 500. Any other Milvus error is re-raised unchanged so genuine
    internal failures still surface as such."""
    msg = (exc.message or str(exc)).lower()
    # Oversized top_k / result window (code=65535; backend-specific ceiling — Lite caps topk
    # at 1024, full Milvus/Zilliz allow far more).
    if exc.code == 65535 and ("topk" in msg or "result window" in msg):
        raise ValueError("top_k_too_large") from exc
    # Vector dimension mismatch: the query (or row) vector dim doesn't match the collection's
    # dense_vec dim — usually a stale cfg.embedding.dim after an embedding-provider swap.
    # Surfaces on Milvus Lite as a numpy matmul "core dimension ... different from" error and
    # on remote Milvus as "expected dim N, got M".
    if "dim" in msg and ("mismatch" in msg or "different from" in msg or "expected dim" in msg):
        raise ValueError("embedding_dim_mismatch") from exc
    raise exc


def _lit(v: str) -> str:
    """Escape a value for a double-quoted Milvus expr literal. connector_uri/object_uri
    derive from user paths/URIs, so an unescaped `"` or `\\` could break out of the
    literal and corrupt the delete/query scope. Control characters such as newlines
    must also be escaped, otherwise legitimate POSIX filenames can make the expr
    unparsable. Mirrors common.retrieval._lit."""
    out: list[str] = []
    for ch in str(v):
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "".join(out)


class MilvusStore:
    def __init__(self, cfg: ServerConfig):
        self.uri = cfg.milvus.uri
        self.token = cfg.milvus.token
        # Empty = use Milvus SDK / server default. Set in cfg.milvus.consistency_level.
        self.consistency_level = cfg.milvus.consistency_level
        # BM25 analyzer config for the `content` field — passed through to
        # Milvus if non-empty. See cfg.milvus.analyzer_params for the schema.
        self.analyzer_params = cfg.milvus.analyzer_params or None
        self.strategy = cfg.milvus.collection_strategy
        self.num_partitions = cfg.milvus.num_partitions
        self.dim = cfg.embedding.dim
        self.client: Optional[MilvusClient] = None

    # mfs default consistency for read paths. Differs from Milvus' SDK default
    # (Bounded, ~5s staleness) because mfs UX is "add a file, search it now".
    # The cost on remote Milvus is 100–500 ms of search latency in exchange
    # for no "I just ingested and search returned nothing" surprise window.
    # On Milvus Lite the choice is moot — no replication means Bounded == Strong.
    # Power users can override via [milvus] consistency_level in server.toml.
    _DEFAULT_CONSISTENCY = "Strong"

    def _cl_kw(self) -> dict[str, Any]:
        """Return {'consistency_level': X} for every read call.

        Uses the user-configured value from cfg.milvus.consistency_level when
        set (empty / unset = our _DEFAULT_CONSISTENCY). Saves every call site
        from a conditional and keeps the read-after-write story consistent.
        """
        level = self.consistency_level or self._DEFAULT_CONSISTENCY
        return {"consistency_level": level}

    def connect(self) -> None:
        kwargs: dict[str, Any] = {"uri": self.uri}
        if self.token:
            kwargs["token"] = self.token
        self.client = MilvusClient(**kwargs)
        # One-line observability: state the FINAL resolved backend (after env overrides) so
        # it's obvious whether this server is talking to Lite or a remote/Zilliz cluster — a
        # silent env override (ZILLIZ_URI) had us mistake a Zilliz server for Lite. Token is
        # never logged; only the host (remote) or db path (Lite).
        if self.is_lite():
            print(f"mfs-server: Milvus backend: Lite ({self.uri})", flush=True)
        else:
            host = urlparse(self.uri).netloc or self.uri
            print(f"mfs-server: Milvus backend: Zilliz/remote {host}", flush=True)

    def is_lite(self) -> bool:
        return not self.uri.startswith("http")

    def resolve_collection(self, namespace_id: str) -> str:
        # version + dim suffix isolates incompatible schemas/models: switching embedding
        # model (dim) or bumping the schema version targets a fresh collection instead of
        # writing into one whose dense_vec dim or fields no longer match.
        suffix = f"v{_COLLECTION_SCHEMA_VERSION}_d{self.dim}"
        if self.strategy == "per_namespace":
            return f"mfs_chunks__{namespace_id}__{suffix}"
        return f"mfs_chunks__{suffix}"

    def _build_schema(self):
        assert self.client is not None
        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("chunk_id", DataType.VARCHAR, max_length=128, is_primary=True)
        schema.add_field("namespace_id", DataType.VARCHAR, max_length=64)
        # connector_uri is the partition key and an upload connector's identity is
        # file://<client_id><client-abs-root> — a deep client path blew past 256 and made
        # the chunk insert fail, so that object silently never indexed. object_uri adds the
        # per-object relpath on top, so it needs even more headroom (cap is Milvus' 65535).
        schema.add_field("connector_uri", DataType.VARCHAR, max_length=512, is_partition_key=True)
        schema.add_field("object_uri", DataType.VARCHAR, max_length=4096)
        # locator is the unified per-chunk identity within an object:
        #   body / code / document chunks  -> {"lines": [start, end]}
        #   structured rows / msgs / issues -> connector PK dict
        #   once-per-object kinds (dir/schema/vlm summaries) -> null
        # The framework reserves "lines" as a key; user-configured
        # locator_fields is rejected at startup if it tries to use it.
        schema.add_field("locator", DataType.JSON, nullable=True)
        content_kw: dict[str, Any] = {"enable_analyzer": True}
        if self.analyzer_params:
            content_kw["analyzer_params"] = self.analyzer_params
        schema.add_field("content", DataType.VARCHAR, max_length=65535, **content_kw)
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
        # AUTOINDEX lets Milvus (Lite + standalone) pick the best index type
        # for the data size. Zilliz Cloud always treats this as AUTOINDEX too,
        # so the same setting works across deployments. Avoids hand-tuning
        # HNSW M/efConstruction.
        ip.add_index(
            field_name="dense_vec",
            index_type="AUTOINDEX",
            metric_type="COSINE",
        )
        ip.add_index(
            field_name="sparse_vec", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25"
        )
        # NOTE: scalar INVERTED indexes (namespace_id/object_uri/chunk_kind)
        # are a filter optimization, not functionally required — Milvus filters work without
        # them (full scan, fine at small scale). Milvus Lite 3.0 rejects scalar create_index
        # ("missing metric_type"), so we add them best-effort post-create (see add_scalar_indexes)
        # and tolerate failure. TODO(perf): revisit when Lite supports scalar indexes.
        return ip

    SCALAR_INDEX_FIELDS = ("namespace_id", "object_uri", "chunk_kind")

    def _add_scalar_indexes(self, name: str) -> None:
        assert self.client is not None
        if self.is_lite():
            return  # Milvus Lite 3.0 rejects scalar INVERTED indexes; skip (filter falls back to scan)
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
            # Always (re-)load: Milvus Lite leaves collections in 'released'
            # state when a new client attaches to an existing .db file (e.g.
            # after a server restart), and search/get/query fail with
            # MilvusException code=101 until load_collection() is called.
            # For remote Milvus the call is effectively idempotent — already-
            # loaded collections stay in memory across client connections.
            self.client.load_collection(name)
            return name
        kwargs: dict[str, Any] = {
            "collection_name": name,
            "schema": self._build_schema(),
            "index_params": self._build_index_params(),
        }
        # num_partitions only meaningful with a partition_key field
        kwargs["num_partitions"] = self.num_partitions
        try:
            # pymilvus (and the embedded milvus-lite engine) log every failed RPC with a full
            # traceback via the logging module before raising; mute all logging just around
            # this call so an analyzer misconfig surfaces as our single clean actionable
            # message rather than a scary double traceback.
            logging.disable(logging.CRITICAL)
            try:
                self.client.create_collection(**kwargs)
            finally:
                logging.disable(logging.NOTSET)
        except Exception as exc:  # noqa: BLE001 — translate analyzer misconfig into a clean exit
            detail = getattr(exc, "message", None) or str(exc)
            if self.analyzer_params and _is_analyzer_config_error(detail):
                # A bad milvus.analyzer_params (unknown tokenizer, or jieba configured but not
                # installed) otherwise crash-loops the server at startup with a raw
                # MilvusException traceback. Fail clean + actionable instead — never silently
                # fall back to 'standard', which would hide the misconfig. os._exit avoids the
                # starlette lifespan re-wrapping a SystemExit into another "startup failed"
                # traceback; nothing is initialized yet at this point, so there is no cleanup
                # to skip.
                print(
                    f"mfs-server: FATAL invalid milvus.analyzer_params "
                    f"{self.analyzer_params!r}: {detail}\n"
                    f"  Supported BM25 tokenizers: 'standard' (default) and 'jieba' (Chinese "
                    f"segmentation; requires the jieba package — `uv pip install jieba`).\n"
                    f'  Fix milvus.analyzer_params in server.toml (e.g. {{type = "jieba"}}) and '
                    f"recreate the collection: the analyzer is applied only at collection "
                    f"creation, so an existing index must be dropped and re-indexed for the "
                    f"change to take effect.",
                    file=sys.stderr,
                    flush=True,
                )
                os._exit(1)
            raise
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
        if not self.client.has_collection(name):
            return  # nothing to purge — a delete must never wedge on a missing collection
        flt = (
            f'namespace_id == "{_lit(namespace_id)}" and connector_uri == "{_lit(connector_uri)}" '
            f'and object_uri == "{_lit(object_uri)}"'
        )
        self.client.delete(collection_name=name, filter=flt)

    def delete_by_connector(self, namespace_id: str, connector_uri: str) -> None:
        assert self.client is not None
        name = self.resolve_collection(namespace_id)
        if not self.client.has_collection(name):
            return  # collection already gone (dropped/reset) — removal must still succeed
        flt = f'namespace_id == "{_lit(namespace_id)}" and connector_uri == "{_lit(connector_uri)}"'
        self.client.delete(collection_name=name, filter=flt)

    def count(self, namespace_id: str, expr: str = "") -> int:
        assert self.client is not None
        name = self.resolve_collection(namespace_id)
        if not self.client.has_collection(name):
            return 0
        rows = self.client.query(
            collection_name=name,
            filter=expr or "chunk_id != ''",
            output_fields=["count(*)"],
            **self._cl_kw(),
        )
        if rows and "count(*)" in rows[0]:
            return int(rows[0]["count(*)"])
        return 0

    def get_chunks_by_object(
        self, namespace_id: str, connector_uri: str, object_uri: str
    ) -> list[dict]:
        """All chunks of an object incl. dense_vec — for rename chunk_id rewrite (reuse
        vectors, zero re-embed)."""
        assert self.client is not None
        name = self.resolve_collection(namespace_id)
        if not self.client.has_collection(name):
            return []
        flt = (
            f'namespace_id == "{_lit(namespace_id)}" and connector_uri == "{_lit(connector_uri)}" '
            f'and object_uri == "{_lit(object_uri)}"'
        )
        return self.client.query(
            collection_name=name,
            filter=flt,
            output_fields=[
                "content",
                "dense_vec",
                "chunk_kind",
                "locator",
                "metadata",
                "indexed_at",
            ],
            **self._cl_kw(),
        )

    def search_dense(
        self,
        namespace_id: str,
        query_vec: list[float],
        limit: int,
        expr: str = "",
        output_fields: Optional[list[str]] = None,
        consistency_level: Optional[str] = None,
    ) -> list[dict]:
        assert self.client is not None
        name = self.resolve_collection(namespace_id)
        if not self.client.has_collection(name):
            return []
        cl_kw = {"consistency_level": consistency_level} if consistency_level else self._cl_kw()
        try:
            res = self.client.search(
                collection_name=name,
                data=[query_vec],
                anns_field="dense_vec",
                limit=limit,
                filter=expr,
                output_fields=output_fields
                or ["chunk_id", "object_uri", "content", "chunk_kind", "locator", "metadata"],
                search_params={"metric_type": "COSINE"},
                **cl_kw,
            )
        except MilvusException as e:
            _reraise_known_milvus_error(e)
        return list(res[0]) if res else []

    # The collection has no top-level "lines" field — line ranges live INSIDE
    # the JSON locator ({"lines": [start, end]} for body/code chunks). Requesting
    # "lines" as an output_field is silently dropped by Milvus Lite (which is
    # why CI never caught it) but fails on remote Milvus with
    #   <MilvusException: code=1100, message=field lines not exist>
    _DEFAULT_OUT = [
        "chunk_id",
        "object_uri",
        "content",
        "chunk_kind",
        "locator",
        "metadata",
    ]

    def sparse_search(
        self,
        namespace_id: str,
        query_text: str,
        limit: int,
        expr: str = "",
        output_fields: Optional[list[str]] = None,
        consistency_level: Optional[str] = None,
    ) -> list[dict]:
        """BM25 keyword search: Milvus turns query_text into a sparse vector via the
        content_bm25 Function. (verified: pymilvus search(data=[text], anns_field='sparse_vec'))."""
        assert self.client is not None
        name = self.resolve_collection(namespace_id)
        if not self.client.has_collection(name):
            return []
        cl_kw = {"consistency_level": consistency_level} if consistency_level else self._cl_kw()
        try:
            res = self.client.search(
                collection_name=name,
                data=[query_text],
                anns_field="sparse_vec",
                limit=limit,
                filter=expr,
                output_fields=output_fields or self._DEFAULT_OUT,
                **cl_kw,
            )
        except MilvusException as e:
            _reraise_known_milvus_error(e)
        return list(res[0]) if res else []

    def hybrid_search(
        self,
        namespace_id: str,
        query_vec: list[float],
        query_text: str,
        limit: int,
        expr: str = "",
        output_fields: Optional[list[str]] = None,
        over_fetch: int = 3,
        consistency_level: Optional[str] = None,
    ) -> list[dict]:
        """dense + BM25 sparse fused with RRF."""
        assert self.client is not None
        name = self.resolve_collection(namespace_id)
        if not self.client.has_collection(name):
            return []
        cl_kw = {"consistency_level": consistency_level} if consistency_level else self._cl_kw()
        k = limit * over_fetch
        rd = AnnSearchRequest(
            data=[query_vec],
            anns_field="dense_vec",
            param={"metric_type": "COSINE"},
            limit=k,
            expr=expr or None,
        )
        rs = AnnSearchRequest(
            data=[query_text], anns_field="sparse_vec", param={}, limit=k, expr=expr or None
        )
        try:
            res = self.client.hybrid_search(
                collection_name=name,
                reqs=[rd, rs],
                ranker=RRFRanker(),
                limit=limit,
                output_fields=output_fields or self._DEFAULT_OUT,
                **cl_kw,
            )
        except MilvusException as e:
            _reraise_known_milvus_error(e)
        return list(res[0]) if res else []
