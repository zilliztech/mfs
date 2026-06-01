"""Anthropic LLM provider (text + vision).

Requires `uv sync --extra anthropic`. Reads ANTHROPIC_API_KEY from env.
"""

from __future__ import annotations

import base64


class AnthropicLlm:
    _DEFAULT_MODEL = "claude-sonnet-4-5-20250929"

    def __init__(self) -> None:
        import anthropic

        self._client = anthropic.AsyncAnthropic()  # ANTHROPIC_API_KEY from env

    async def chat(
        self,
        prompt: str,
        *,
        model: str | None = None,
        max_tokens: int = 800,
        temperature: float = 0.3,
    ) -> str:
        resp = await self._client.messages.create(
            model=model or self._DEFAULT_MODEL,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        # content is a list of content blocks; pull text from the first.
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                return block.text
        return ""

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
        resp = await self._client.messages.create(
            model=model or self._DEFAULT_MODEL,
            max_tokens=max_tokens,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": mime,
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                return block.text
        return ""
