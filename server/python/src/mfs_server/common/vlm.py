"""VLM client: image bytes -> description text, memoized in transformation cache.

Multi-provider (openai/anthropic/gemini); the configured [description].provider drives
the lookup. Result is stored as a vlm_text artifact + indexed as a vlm_description chunk.

This client holds NO concurrency control of its own: the [description].concurrency ceiling
is enforced by the shared DescriptionConcurrencyGate (engine/producers/base.py) at every
call site (ImageChunksProducer, the Reduce SummaryWorker), so a single process-wide budget
governs in-flight VLM calls regardless of where describe() is invoked (§5.5).
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
        self.model = cfg.description.model
        self.prompt = cfg.description.prompt
        self.provider = cfg.description.provider
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
        h = sha1_hex(data)
        # The prompt is part of the cache identity: changing [description].prompt must
        # re-describe rather than return a description produced under the old prompt.
        key = cache_key(
            h,
            "vlm",
            self.provider,
            self.model,
            self.version,
            config=sha1_hex(self.prompt.encode()),
        )
        ran = False

        async def _compute() -> bytes:
            nonlocal ran
            ran = True
            llm = self._ensure_llm()
            desc = await llm.vision(
                self.prompt, data, self.mime_for(ext), model=self.model, max_tokens=400
            )
            return desc.encode()

        # get_or_compute holds a per-key lock so concurrent callers that all miss the same
        # image (Map ImageChunksProducer + Reduce SummaryWorker) fire the provider EXACTLY
        # once (§3.4), instead of each issuing the expensive VLM call.
        out = await self.tx_cache.get_or_compute(
            key,
            _compute,
            kind="vlm",
            input_hash=h,
            provider=self.provider,
            model=self.model,
            model_version=self.version,
        )
        if ran:
            self.api_calls += 1
        else:
            self.cache_hits += 1
        return out.decode("utf-8", errors="replace")
