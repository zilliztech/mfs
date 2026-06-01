"""VLM client: image bytes -> description text, memoized in transformation cache.

Multi-provider (openai/anthropic/gemini); the configured vlm.provider drives
the lookup. Result is stored as a vlm_text artifact + indexed as a
vlm_description chunk.
"""

from __future__ import annotations

from typing import Any

from ..config import ServerConfig
from ..storage.ids import cache_key, sha1_hex
from ..storage.transformation_cache import TransformationCache
from .llm import get_provider

_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


class CachingVlmClient:
    def __init__(self, cfg: ServerConfig, tx_cache: TransformationCache):
        self.model = cfg.vlm.model
        self.prompt = cfg.vlm.prompt
        self.provider = cfg.vlm.provider
        self.version = "1"
        self.tx_cache = tx_cache
        # Lazy: server boots w/o any LLM key.
        self._llm: Any = None
        self.api_calls = 0
        self.cache_hits = 0

    def _ensure_llm(self) -> Any:
        if self._llm is None:
            self._llm = get_provider(self.provider)
        return self._llm

    def mime_for(self, ext: str) -> str:
        return _MIME.get(ext.lower(), "image/png")

    async def describe(self, data: bytes, ext: str) -> str:
        key = cache_key(sha1_hex(data), "vlm", self.provider, self.model, self.version)
        cached = await self.tx_cache.batch_get([key])
        if cached[key] is not None:
            self.cache_hits += 1
            return cached[key].decode("utf-8", errors="replace")
        llm = self._ensure_llm()
        desc = await llm.vision(
            self.prompt,
            data,
            self.mime_for(ext),
            model=self.model,
            max_tokens=400,
        )
        self.api_calls += 1
        await self.tx_cache.batch_put(
            [
                {
                    "cache_key": key,
                    "kind": "vlm",
                    "input_hash": sha1_hex(data),
                    "provider": self.provider,
                    "model": self.model,
                    "model_version": self.version,
                    "output_bytes": desc.encode(),
                    "output_size": len(desc.encode()),
                }
            ]
        )
        return desc
