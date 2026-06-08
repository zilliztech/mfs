"""Converter client: file bytes -> markdown, memoized in transformation cache
. markitdown default (one lib covers PDF/DOCX/PPTX/XLSX/HTML).
Result also stored as converted_md artifact by the engine. Web crawler does NOT use
this path (its HTML->md is backend-coupled inside the connector).
"""

from __future__ import annotations

import asyncio
import os
import tempfile

from ..config import ServerConfig
from ..storage.ids import cache_key, sha1_hex
from ..storage.transformation_cache import TransformationCache

# extensions the framework converter turns into markdown (file-form documents)
CONVERT_EXTS = {".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".html", ".htm"}


class CachingConverterClient:
    def __init__(self, cfg: ServerConfig, tx_cache: TransformationCache):
        self.default = cfg.conversion.default  # "markitdown"
        self.provider = "markitdown"
        self.version = "1"
        self.tx_cache = tx_cache
        self._md = None
        self.api_calls = 0
        self.cache_hits = 0

    def _key(self, data: bytes) -> str:
        return cache_key(sha1_hex(data), "convert", self.provider, self.default, self.version)

    async def convert(self, data: bytes, ext: str) -> str:
        key = self._key(data)
        h = sha1_hex(data)
        ran = False

        async def _compute() -> bytes:
            nonlocal ran
            ran = True
            md = await asyncio.to_thread(self._convert_sync, data, ext)
            return md.encode()

        # per-key lock: the Map text producer and the Reduce SummaryWorker can both miss the
        # same document hash concurrently; with the lock the (expensive) conversion runs once
        # (§3.4).
        out = await self.tx_cache.get_or_compute(
            key,
            _compute,
            kind="convert",
            input_hash=h,
            provider=self.provider,
            model=self.default,
            model_version=self.version,
        )
        if ran:
            self.api_calls += 1
        else:
            self.cache_hits += 1
        return out.decode("utf-8", errors="replace")

    def _convert_sync(self, data: bytes, ext: str) -> str:
        from markitdown import MarkItDown

        if self._md is None:
            self._md = MarkItDown()
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
            f.write(data)
            path = f.name
        try:
            return self._md.convert(path).text_content
        finally:
            os.remove(path)
