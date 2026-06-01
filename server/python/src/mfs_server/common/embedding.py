"""Embedding client with transformation-cache memoization.

CachingEmbeddingClient.batch_embed: cache_key lookup -> miss-only API call (batched)
-> write back. Vectors stored in tx cache as packed float32. Tracks api_calls /
cache_hits for observability/tests.

Two providers:
- "onnx" (default): local ONNX model via onnxruntime + huggingface_hub.
  Zero API key needed; model downloads to $MFS_HOME/onnx-cache/ on first use.
- "openai": OpenAI hosted embedding API (reads OPENAI_API_KEY from env).
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
        """Local ONNX embedding: onnxruntime + huggingface_hub. No API key.

        Tokenizer + model are downloaded from the Hugging Face Hub on first
        use and cached under $MFS_HOME/onnx-cache/, so the model survives
        container restarts when /data is a mounted volume."""
        if self._client is None:
            self._client = _OnnxEmbedder(self.model)
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

            embedder = self._ensure_onnx()
            out: list[list[float]] = []
            for i in range(0, len(texts), self.batch_size):
                batch = texts[i : i + self.batch_size]
                vecs = await asyncio.to_thread(embedder.encode, batch)
                self.api_calls += len(batch)
                out.extend(vecs)
            return out
        if self.provider != "openai":
            raise RuntimeError(f"embedding provider {self.provider} not supported")
        client = self._ensure_client()
        out = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            resp = await client.embeddings.create(model=self.model, input=batch)
            self.api_calls += len(batch)
            out.extend([d.embedding for d in resp.data])
        return out


# ───────────────────── local ONNX embedder ──────────────────────────────────
#
# Direct onnxruntime call. Supports two ONNX export conventions:
#   - "dense_vecs" output (pre-pooled, e.g. bge-m3-onnx-int8) — used as-is
#   - "last_hidden_state" output — CLS pooling + L2 normalize applied
#
# Tokenizer + model files come from the Hugging Face Hub. We try
# local_files_only=True first so sandboxed / offline environments don't make
# HEAD calls; on the first run we fall back to a network download.


class _OnnxEmbedder:
    """Local ONNX embedding: tokenizer + onnxruntime session for one model."""

    def __init__(self, model: str) -> None:
        import os
        from pathlib import Path

        import onnxruntime as ort
        from huggingface_hub import hf_hub_download, list_repo_files
        from tokenizers import Tokenizer

        cache_dir = Path(os.environ.get("MFS_HOME") or (Path.home() / ".mfs")) / "onnx-cache"
        cache_dir.mkdir(parents=True, exist_ok=True)

        tok_path, model_path = self._download(model, cache_dir, hf_hub_download, list_repo_files)

        self._tokenizer = Tokenizer.from_file(tok_path)
        # BGE family pads with id 1 ("<pad>"); 8K context covers bge-m3 fully.
        self._tokenizer.enable_padding(pad_id=1, pad_token="<pad>")
        self._tokenizer.enable_truncation(max_length=8192)

        self._session = ort.InferenceSession(model_path)
        self._output_names = [o.name for o in self._session.get_outputs()]
        self._has_dense_vecs = "dense_vecs" in self._output_names

    @staticmethod
    def _download(model: str, cache_dir, hf_hub_download, list_repo_files):
        """Download tokenizer + ONNX model, preferring local cache.

        Returns (tokenizer_path, onnx_path).
        """
        # Pass cache_dir explicitly so artifacts live under $MFS_HOME instead
        # of $HF_HOME / ~/.cache/huggingface, keeping all server state
        # under the mounted /data volume.
        kw = {"cache_dir": str(cache_dir)}

        # Try offline first (HEAD-call-free; matters for sandboxed/no-network runs).
        try:
            tok_path = hf_hub_download(model, "tokenizer.json", local_files_only=True, **kw)
            for candidate in ("model_quantized.onnx", "model.onnx"):
                try:
                    model_path = hf_hub_download(model, candidate, local_files_only=True, **kw)
                    # Best-effort: pull the external data sidecar if present
                    import contextlib

                    with contextlib.suppress(Exception):
                        hf_hub_download(model, candidate + "_data", local_files_only=True, **kw)
                    return tok_path, model_path
                except Exception:  # noqa: BLE001
                    continue
            raise FileNotFoundError("no cached ONNX file")
        except Exception:  # noqa: BLE001
            pass

        # Online: first run or evicted cache.
        tok_path = hf_hub_download(model, "tokenizer.json", **kw)
        repo_files = list_repo_files(model)
        onnx_files = [f for f in repo_files if f.endswith(".onnx")]
        if not onnx_files:
            raise ValueError(f"no .onnx files found in {model}")
        if "model_quantized.onnx" in onnx_files:
            onnx_file = "model_quantized.onnx"
        elif "model.onnx" in onnx_files:
            onnx_file = "model.onnx"
        else:
            onnx_file = onnx_files[0]
        # Some int8 exports split weights into an external _data sidecar.
        data_file = onnx_file + "_data"
        if data_file in repo_files:
            hf_hub_download(model, data_file, **kw)
        model_path = hf_hub_download(model, onnx_file, **kw)
        return tok_path, model_path

    def encode(self, texts: list[str]) -> list[list[float]]:
        import numpy as np

        encoded = self._tokenizer.encode_batch(texts)
        input_ids = np.array([e.ids for e in encoded])
        attention_mask = np.array([e.attention_mask for e in encoded])
        outputs = self._session.run(
            None, {"input_ids": input_ids, "attention_mask": attention_mask}
        )

        if self._has_dense_vecs:
            # Pre-pooled dense vectors (e.g. bge-m3-onnx-int8)
            embeddings = outputs[self._output_names.index("dense_vecs")]
        else:
            # CLS pooling on last_hidden_state
            embeddings = outputs[self._output_names.index("last_hidden_state")][:, 0, :]

        # L2 normalize → cosine-similarity-ready vectors
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        normalized = embeddings / norms
        return normalized.tolist()
