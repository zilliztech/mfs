"""Anthropic Messages LLM/VLM provider.

Requires: ``pip install 'mfs[llm-anthropic]'`` or ``uv add 'mfs[llm-anthropic]'``
Environment variables:
    ANTHROPIC_API_KEY — required
"""

from __future__ import annotations

import base64
import os


class AnthropicLLM:
    """Anthropic Messages API wrapper.

    Implements both :class:`LLMProvider` and :class:`VLMCapable`. All current
    Claude 3 / 3.5 / 4 models accept image inputs, so vision is always
    available when the API call succeeds.
    """

    _DEFAULT_MAX_TOKENS = 1024

    def __init__(
        self,
        model: str = "claude-3-5-haiku-latest",
        *,
        api_key: str | None = None,
        max_tokens: int = 0,
    ) -> None:
        try:
            import anthropic
        except ImportError as exc:
            raise ImportError(
                "Anthropic LLM provider requires anthropic. "
                "Install with: pip install 'mfs[llm-anthropic]' "
                "or: uv add 'mfs[llm-anthropic]'"
            ) from exc

        effective_api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not effective_api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set and no api_key provided"
            )
        self._client = anthropic.Anthropic(api_key=effective_api_key)
        self._model = model
        self._max_tokens = max_tokens if max_tokens > 0 else self._DEFAULT_MAX_TOKENS

    @property
    def model_name(self) -> str:
        return self._model

    def generate(self, prompt: str, *, system: str | None = None) -> str:
        kwargs: dict = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        resp = self._client.messages.create(**kwargs)
        return _extract_text(resp)

    def describe_image(self, image_path: str, *, prompt: str | None = None) -> str:
        from .utils import read_image_bytes

        raw, mime = read_image_bytes(image_path)
        b64 = base64.standard_b64encode(raw).decode("ascii")
        prompt_text = prompt or "Describe this image in detail."
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
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
                        {"type": "text", "text": prompt_text},
                    ],
                }
            ],
        )
        return _extract_text(resp)


def _extract_text(resp) -> str:
    """Concatenate text blocks from an Anthropic Messages response."""
    parts: list[str] = []
    for block in getattr(resp, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "".join(parts)
