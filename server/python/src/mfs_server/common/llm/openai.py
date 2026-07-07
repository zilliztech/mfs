"""OpenAI LLM provider (text + vision).

Reads OPENAI_API_KEY / OPENAI_BASE_URL from env.
"""

from __future__ import annotations

import base64
import os

from openai import AsyncOpenAI
from openai.types.chat import (
    ChatCompletionContentPartImageParam,
    ChatCompletionContentPartTextParam,
    ChatCompletionUserMessageParam,
)
from openai.types.chat.chat_completion_content_part_image_param import ImageURL


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
            messages=[ChatCompletionUserMessageParam(role="user", content=prompt)],
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
        image_url = ImageURL(url=f"data:{mime};base64,{b64}")
        resp = await self._client.chat.completions.create(
            model=model or self._DEFAULT_MODEL,
            messages=[
                ChatCompletionUserMessageParam(
                    role="user",
                    content=[
                        ChatCompletionContentPartTextParam(type="text", text=prompt),
                        ChatCompletionContentPartImageParam(type="image_url", image_url=image_url),
                    ],
                )
            ],
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content or ""
