"""Embedding client with transformation-cache memoization.

CachingEmbeddingClient.batch_embed: cache_key lookup → miss-only call to the
configured provider → write back. Vectors stored as packed float32. Tracks
api_calls / cache_hits for observability/tests.

Provider selection lives in `mfs_server.common.embeddings` (lazy imports).
Supported names today: onnx (default), openai, google, voyage, ollama, local.
"""

from __future__ import annotations

import array
from typing import Any

from ..config import ServerConfig
from ..storage.ids import cache_key, sha1_hex
from ..storage.transformation_cache import TransformationCache
from .embeddings import get_provider

_STARTUP_PRELOAD_PROVIDERS = {"onnx", "local"}


def encode_vec(v: list[float]) -> bytes:
    return array.array("f", v).tobytes()


def decode_vec(b: bytes) -> list[float]:
    a = array.array("f")
    a.frombytes(b)
    return list(a)


class CachingEmbeddingClient:
    def __init__(self, cfg: ServerConfig, tx_cache: TransformationCache):
        self.provider_name = cfg.embedding.provider
        self.model = cfg.embedding.model
        self.version = "1"
        self.dim = cfg.embedding.dim
        self.tx_cache = tx_cache
        # Lazy unless the server/worker startup path explicitly preloads a local
        # downloadable provider. Browse / ls / cat / grep callers still do not need it.
        self._provider: Any = None
        self._dim_warned = False
        self.api_calls = 0
        self.cache_hits = 0

    @property
    def provider(self) -> str:
        # backwards compat for call sites that read .provider directly
        return self.provider_name

    def _ensure_provider(self) -> Any:
        if self._provider is None:
            self._provider = get_provider(self.provider_name, self.model)
            self._warn_dim_mismatch_once()
        return self._provider

    def should_preload_on_server_start(self) -> bool:
        return self.provider_name in _STARTUP_PRELOAD_PROVIDERS

    def preload_provider(self) -> None:
        self._ensure_provider()

    def _warn_dim_mismatch_once(self) -> None:
        """Warn (once) if cfg.embedding.dim — which names the Milvus collection — doesn't
        match the provider's actual output dim. Deferred to the first provider build (not
        provider preload) so lightweight Engine users do not download the model just to read
        .dimension. A stale dim after a provider swap still surfaces hard at search/index
        time via the embedding_dim_mismatch envelope — this is the friendly early heads-up
        that fires once the model is loaded anyway. Never fatal."""
        if self._dim_warned:
            return
        self._dim_warned = True
        try:
            actual = int(self._provider.dimension)
        except Exception:  # noqa: BLE001 — can't read dim: skip, don't break embedding
            return
        if actual != self.dim:
            print(
                f"mfs-server: WARNING embedding dim mismatch — configured dim={self.dim} "
                f"but provider '{self.provider_name}'/'{self.model}' emits dim={actual}. "
                f"Search/index will fail against the dim={self.dim} collection; re-run "
                f"`mfs-server setup --section embedding` or re-index into a fresh collection.",
                flush=True,
            )

    def _key(self, text: str) -> str:
        return cache_key(
            sha1_hex(text.encode()), "embedding", self.provider_name, self.model, self.version
        )

    async def batch_embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        keys = [self._key(t) for t in texts]
        cached = await self.tx_cache.batch_get(keys)
        result: list[list[float] | None] = [None] * len(texts)
        miss_idx: list[int] = []
        for i, k in enumerate(keys):
            if cached[k] is not None:
                result[i] = decode_vec(cached[k])
                self.cache_hits += 1
            else:
                miss_idx.append(i)
        if miss_idx:
            miss_texts = [texts[i] for i in miss_idx]
            vecs = await self._embed_api(miss_texts)
            puts = []
            for j, i in enumerate(miss_idx):
                result[i] = vecs[j]
                puts.append(
                    {
                        "cache_key": keys[i],
                        "kind": "embedding",
                        "input_hash": sha1_hex(texts[i].encode()),
                        "provider": self.provider_name,
                        "model": self.model,
                        "model_version": self.version,
                        "output_bytes": encode_vec(vecs[j]),
                        "output_size": len(vecs[j]) * 4,
                    }
                )
            await self.tx_cache.batch_put(puts)
        return result  # type: ignore[return-value]

    async def _embed_api(self, texts: list[str]) -> list[list[float]]:
        # Provider handles its own internal batching (see e.g. utils.batched_embed).
        provider = self._ensure_provider()
        vecs = await provider.embed(texts)
        self.api_calls += len(texts)
        return vecs
