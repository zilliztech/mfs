"""Summary client: condense input into a short text used as a
`directory_summary` / `schema_summary` chunk, improving recall for holistic queries.
Multi-provider (openai/anthropic/gemini), memoized in the transformation cache
(kind='summary', keyed on input hash + provider/model/version → model change
re-summarizes). Lazy provider so the server boots without any API key.

This client holds NO concurrency control of its own: the [summary].concurrency ceiling is
enforced by the shared SummaryConcurrencyGate (engine/producers/base.py) at every call site
(TableSchemaProducer, the Reduce SummaryWorker), so a single process-wide budget governs
in-flight summary calls regardless of where summarize() is invoked (§5.5).
"""

from __future__ import annotations

from typing import Any

from ..config import ServerConfig
from ..storage.ids import cache_key, sha1_hex
from ..storage.transformation_cache import TransformationCache
from .llm import get_provider

_PROMPTS = {
    "schema_summary": "Describe this table/collection schema for search: what the table "
    "likely holds, and the meaning of its key columns. 2-4 sentences.",
    "directory_summary": "Below are the files (with content excerpts) and sub-directory "
    "summaries contained in one directory. In 2-4 sentences, describe "
    "what this directory holds and the role it plays in the project. "
    "Plain text only.",
}


class CachingSummaryClient:
    def __init__(self, cfg: ServerConfig, tx_cache: TransformationCache):
        self.cfg = cfg.summary
        self.enabled = cfg.summary.enabled
        self.model = cfg.summary.model
        self.provider = cfg.summary.provider
        self.version = "1"
        self.max_tokens = cfg.summary.max_tokens
        self.tx_cache = tx_cache
        # Lazy: built on first call so the server boots without provider creds.
        self._llm: Any = None
        self.api_calls = 0
        self.cache_hits = 0

    def _ensure_llm(self) -> Any:
        if self._llm is None:
            self._llm = get_provider(self.provider)
        return self._llm

    async def summarize(self, text: str, kind: str = "directory_summary") -> str:
        if not text.strip():
            return ""
        prompt = _PROMPTS.get(kind, _PROMPTS["directory_summary"])
        h = sha1_hex((kind + "\n" + text).encode())
        # max_tokens is part of the cache identity: a different output budget yields a
        # different summary, so it must not return one cached under another budget.
        key = cache_key(
            h,
            "summary",
            self.provider,
            self.model,
            self.version,
            config=str(self.max_tokens),
        )
        ran = False

        async def _compute() -> bytes:
            nonlocal ran
            ran = True
            llm = self._ensure_llm()
            # caller truncates to summary.max_input_kb; this is just a hard safety ceiling
            out = await llm.chat(
                f"{prompt}\n\n---\n{text[:200_000]}", model=self.model, max_tokens=self.max_tokens
            )
            return out.encode()

        # per-key lock: concurrent callers that miss the same input (TableSchemaProducer +
        # Reduce SummaryWorker) compute it once (§3.4).
        out = await self.tx_cache.get_or_compute(
            key,
            _compute,
            kind="summary",
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
