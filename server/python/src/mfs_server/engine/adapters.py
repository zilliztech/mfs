"""Thin adapters binding engine.py's existing services to the pipeline / producer
Protocols. No business logic — each method is a pass-through (plus the sync→async
`asyncio.to_thread` hop for the blocking Milvus / artifact-store calls, and the
bytes↔vector (de)serialization the transformation cache needs).

Wiring map:
  EmbedderAdapter      -> pipeline.Embedder        (CachingEmbeddingClient)
  MilvusSinkAdapter    -> pipeline.MilvusSink       (storage.milvus.MilvusStore)
  TxCacheAdapter       -> pipeline.TxCacheLike      (storage.transformation_cache)
  ArtifactStoreAdapter -> producers.base.ArtifactStore (storage.artifact_cache + meta)
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from ..common.embedding import decode_vec, encode_vec


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EmbedderAdapter:
    """Adapt an embed callable to pipeline.Embedder.

    Wraps a raw `async (texts) -> vectors` function — for the real wiring (driver.py)
    that is CachingEmbeddingClient._embed_api (the underlying provider call), so the
    EmbedConsumer's own TxCacheAdapter is the single embed cache and there is no
    double-caching. A plain CachingEmbeddingClient.batch_embed also fits this shape."""

    def __init__(self, embed_fn: Callable[[list[str]], Awaitable[list[list[float]]]]):
        self._embed = embed_fn

    async def batch_embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return await self._embed(texts)


class MilvusSinkAdapter:
    """Adapt the (synchronous) MilvusStore to pipeline.MilvusSink, binding namespace_id
    and hopping the blocking calls off the event loop."""

    def __init__(self, milvus: Any, namespace_id: str):
        self._milvus = milvus
        self._ns = namespace_id

    async def upsert(self, rows: list[dict]) -> None:
        if not rows:
            return
        await asyncio.to_thread(self._milvus.upsert, self._ns, rows)

    async def delete_by_object(self, connector_uri: str, object_uri: str) -> None:
        await asyncio.to_thread(
            self._milvus.delete_by_object, self._ns, connector_uri, object_uri
        )


class TxCacheAdapter:
    """Adapt TransformationCache to pipeline.TxCacheLike (vectors instead of bytes).

    batch_get decodes stored float32 bytes to vectors; batch_put encodes them back and
    fills the cache-row metadata. The simplified TxCacheLike.batch_put({key: vec}) gives
    no input text, so `input_hash` is stored empty — it's informational only (lookups key
    on cache_key), so this is lossless for correctness."""

    def __init__(
        self,
        tx_cache: Any,
        *,
        kind: str = "embedding",
        provider: str = "",
        model: str = "",
        version: str = "1",
    ):
        self._tx = tx_cache
        self._kind = kind
        self._provider = provider
        self._model = model
        self._version = version

    async def batch_get(self, keys: list[str]) -> dict[str, Optional[list[float]]]:
        raw = await self._tx.batch_get(keys)
        return {k: (decode_vec(v) if v is not None else None) for k, v in raw.items()}

    async def batch_put(self, items: dict[str, list[float]]) -> None:
        if not items:
            return
        entries = [
            {
                "cache_key": key,
                "kind": self._kind,
                "input_hash": "",
                "provider": self._provider,
                "model": self._model,
                "model_version": self._version,
                "output_bytes": encode_vec(vec),
                "output_size": len(vec) * 4,
            }
            for key, vec in items.items()
        ]
        await self._tx.batch_put(entries)


class ArtifactStoreAdapter:
    """Adapt the artifact_cache (bytes store) + metadata store to producers.base
    .ArtifactStore. Mirrors engine._put_artifact's two writes — the bytes plus the
    artifact_cache index row so `mfs cat` / `head` can find the derived artifact — but
    leaves the throttled LRU eviction sweep to the engine (step 4)."""

    def __init__(self, artifact_cache: Any, meta: Any):
        self._cache = artifact_cache
        self._meta = meta

    async def put_artifact(self, namespace_id: str, object_uri: str, kind: str, data: bytes) -> None:
        path = await asyncio.to_thread(
            self._cache.put_artifact, namespace_id, object_uri, kind, data
        )
        now = _now()
        fp = hashlib.sha1(data).hexdigest()
        await self._meta.execute(
            "INSERT INTO artifact_cache (namespace_id, object_uri, artifact_kind, storage_path, "
            " fingerprint, size_bytes, built_at, last_accessed) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(namespace_id, object_uri, artifact_kind) DO UPDATE SET "
            " storage_path=excluded.storage_path, fingerprint=excluded.fingerprint, "
            " size_bytes=excluded.size_bytes, built_at=excluded.built_at, "
            " last_accessed=excluded.last_accessed",
            (namespace_id, object_uri, kind, str(path), fp, len(data), now, now),
        )

    async def get_artifact(self, namespace_id: str, object_uri: str, kind: str) -> Optional[bytes]:
        return await asyncio.to_thread(
            self._cache.get_artifact, namespace_id, object_uri, kind
        )

    def artifact_path(self, namespace_id: str, object_uri: str, kind: str) -> str:
        return str(self._cache.artifact_path(namespace_id, object_uri, kind))
