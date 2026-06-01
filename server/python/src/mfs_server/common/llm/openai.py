"""OpenAI LLM provider (text + vision).

Reads OPENAI_API_KEY / OPENAI_BASE_URL from env.
"""

from __future__ import annotations

import base64
import os

from openai import AsyncOpenAI


class OpenAILlm:
    _DEFAULT_MODEL = "gpt-4o-mini"

    def __init__(self) -> None:
        kwargs: dict = {}
        base_url = os.environ.get("OPENAI_BASE_URL")
        if base_url:
            kwargs["base_url"] = base_url
        # api_key auto-read from OPENAI_API_KEY by the SDK
        self._client = AsyncOpenAI(**kwargs)

    async def chat(
        self,
        prompt: str,
        *,
        model: str | None = None,
        max_tokens: int = 800,
        temperature: float = 0.3,
    ) -> str:
        resp = await self._client.chat.completions.create(
            model=model or self._DEFAULT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return resp.choices[0].message.content or ""

    async def vision(
        self,
        prompt: str,
        image_bytes: bytes,
        mime: str,
        *,
        model: str | None = None,
        max_tokens: int = 800,
    ) -> str:
        b64 = base64.b64encode(image_bytes).decode("ascii")
        resp = await self._client.chat.completions.create(
            model=model or self._DEFAULT_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    ],
                }
            ],
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content or ""
