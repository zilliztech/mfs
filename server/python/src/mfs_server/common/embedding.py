"""Embedding client with transformation-cache memoization.

CachingEmbeddingClient.batch_embed: cache_key lookup -> miss-only API call (batched)
-> write back. Vectors stored in tx cache as packed float32. OpenAI provider (reads
OPENAI_API_KEY from env). Tracks api_calls / cache_hits for observability/tests.
"""

from __future__ import annotations

import array

from openai import AsyncOpenAI

from ..config import ServerConfig
from ..storage.ids import cache_key, sha1_hex
from ..storage.transformation_cache import TransformationCache


def encode_vec(v: list[float]) -> bytes:
    return array.array("f", v).tobytes()


def decode_vec(b: bytes) -> list[float]:
    a = array.array("f")
    a.frombytes(b)
    return list(a)


class CachingEmbeddingClient:
    def __init__(self, cfg: ServerConfig, tx_cache: TransformationCache):
        self.provider = cfg.embedding.provider
        self.model = cfg.embedding.model
        self.version = "1"
        self.dim = cfg.embedding.dim
        self.batch_size = cfg.embedding.batch_size
        self.tx_cache = tx_cache
        self._client = None  # lazy: built on first API call so the server boots
        # without OPENAI_API_KEY (browse/ls/cat/grep don't need embeddings)
        # observability
        self.api_calls = 0
        self.cache_hits = 0

    def _ensure_client(self):
        if self._client is None:
            self._client = AsyncOpenAI()
        return self._client

    def _ensure_onnx(self):
        """Local ONNX embedding via fastembed (onnxruntime; no API key). Model is
        downloaded + cached on first use."""
        if self._client is None:
            from fastembed import TextEmbedding

            self._client = TextEmbedding(model_name=self.model)
        return self._client

    def _key(self, text: str) -> str:
        return cache_key(
            sha1_hex(text.encode()), "embedding", self.provider, self.model, self.version
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
                        "provider": self.provider,
                        "model": self.model,
                        "model_version": self.version,
                        "output_bytes": encode_vec(vecs[j]),
                        "output_size": len(vecs[j]) * 4,
                    }
                )
            await self.tx_cache.batch_put(puts)
        return result  # type: ignore[return-value]

    async def _embed_api(self, texts: list[str]) -> list[list[float]]:
        if self.provider == "onnx":
            import asyncio

            model = self._ensure_onnx()
            vecs = await asyncio.to_thread(lambda: [v.tolist() for v in model.embed(texts)])
            self.api_calls += len(texts)
            return vecs
        if self.provider != "openai":
            raise RuntimeError(f"embedding provider {self.provider} not supported")
        client = self._ensure_client()
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            resp = await client.embeddings.create(model=self.model, input=batch)
            self.api_calls += len(batch)
            out.extend([d.embedding for d in resp.data])
        return out
