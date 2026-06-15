"""Converter client: file bytes -> markdown. markitdown default (one lib covers
PDF/DOCX/PPTX/XLSX/HTML).

This is a simple, deterministic file-format conversion — not a model call — so it is
not memoized in the transformation cache (which is reserved for model outputs:
embeddings, VLM descriptions, summaries). Its result is cached at the artifact layer
instead, per object. Web crawler does NOT use this path (its HTML->md is
backend-coupled inside the connector).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile

from ..config import ServerConfig

# extensions the framework converter turns into markdown (file-form documents)
CONVERT_EXTS = {".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".html", ".htm"}


class ConverterClient:
    def __init__(self, cfg: ServerConfig):
        self.default = cfg.conversion.default  # "markitdown"
        self.provider = "markitdown"
        self.version = "1"
        self._md = None

    def identity(self) -> str:
        """The converter's self-described identity — provider + library + version. Fold any
        converter parameters in here (as the model clients fold provider/model/version/prompt
        into their transformation-cache key) so changing them invalidates cached conversions.
        markitdown currently exposes no tunable params, so this is just the version tuple."""
        return f"{self.provider}.{self.default}.{self.version}"

    def currency(self, data: bytes) -> str:
        """Artifact-cache freshness token for converting `data`: source content hash + the
        converter identity. The Object Lane and Job Lane compute it identically, so the Job
        Lane reuses the Object Lane's `converted_md` only when BOTH the source content and the
        converter identity match — a changed source or an upgraded converter misses and
        re-converts."""
        return f"{hashlib.sha1(data).hexdigest()}:{self.identity()}"

    async def convert(self, data: bytes, ext: str) -> str:
        return await asyncio.to_thread(self._convert_sync, data, ext)

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
