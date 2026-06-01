"""VLM client: image bytes -> description text, memoized in transformation cache
. OpenAI chat vision (gpt-4o-mini, image_url base64 data URL — verified).
Result also stored as vlm_text artifact + indexed as a vlm_description chunk.
"""

from __future__ import annotations

import base64

from openai import AsyncOpenAI

from ..config import ServerConfig
from ..storage.ids import cache_key, sha1_hex
from ..storage.transformation_cache import TransformationCache

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
        self.provider = "openai"
        self.version = "1"
        self.tx_cache = tx_cache
        self._client = None  # lazy: built on first call (server boots w/o OPENAI key)
        self.api_calls = 0
        self.cache_hits = 0

    def _ensure_client(self):
        if self._client is None:
            if self.provider != "openai":
                raise RuntimeError("vlm provider not supported")
            self._client = AsyncOpenAI()
        return self._client

    def mime_for(self, ext: str) -> str:
        return _MIME.get(ext.lower(), "image/png")

    async def describe(self, data: bytes, ext: str) -> str:
        key = cache_key(sha1_hex(data), "vlm", self.provider, self.model, self.version)
        cached = await self.tx_cache.batch_get([key])
        if cached[key] is not None:
            self.cache_hits += 1
            return cached[key].decode("utf-8", errors="replace")
        client = self._ensure_client()
        b64 = base64.b64encode(data).decode()
        url = f"data:{self.mime_for(ext)};base64,{b64}"
        resp = await client.chat.completions.create(
            model=self.model,
            max_tokens=400,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": self.prompt},
                        {"type": "image_url", "image_url": {"url": url}},
                    ],
                }
            ],
        )
        desc = resp.choices[0].message.content or ""
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
