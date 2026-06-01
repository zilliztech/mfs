"""Summary client: condense input into a short text used as a
`directory_summary` / `schema_summary` chunk, improving recall for holistic queries.
OpenAI chat (gpt-4o-mini), memoized in the transformation cache (kind='summary', keyed
on input hash + provider/model/version → model change re-summarizes). Lazy client so the
server boots without OPENAI_API_KEY.
"""

from __future__ import annotations

from openai import AsyncOpenAI

from ..config import ServerConfig
from ..storage.ids import cache_key, sha1_hex
from ..storage.transformation_cache import TransformationCache

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
        self._client = None
        self.api_calls = 0
        self.cache_hits = 0

    def _ensure_client(self):
        if self._client is None:
            if self.provider != "openai":
                raise RuntimeError(f"summary provider {self.provider} not supported")
            self._client = AsyncOpenAI()
        return self._client

    async def summarize(self, text: str, kind: str = "directory_summary") -> str:
        if not text.strip():
            return ""
        prompt = _PROMPTS.get(kind, _PROMPTS["directory_summary"])
        key = cache_key(
            sha1_hex((kind + "\n" + text).encode()),
            "summary",
            self.provider,
            self.model,
            self.version,
        )
        cached = await self.tx_cache.batch_get([key])
        if cached[key] is not None:
            self.cache_hits += 1
            return cached[key].decode("utf-8", errors="replace")
        client = self._ensure_client()
        resp = await client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            # caller truncates to summary.max_input_kb; this is just a hard safety ceiling
            messages=[{"role": "user", "content": f"{prompt}\n\n---\n{text[:200_000]}"}],
        )
        out = resp.choices[0].message.content or ""
        self.api_calls += 1
        await self.tx_cache.batch_put(
            [
                {
                    "cache_key": key,
                    "kind": "summary",
                    "input_hash": sha1_hex(text.encode()),
                    "provider": self.provider,
                    "model": self.model,
                    "model_version": self.version,
                    "output_bytes": out.encode(),
                    "output_size": len(out.encode()),
                }
            ]
        )
        return out
