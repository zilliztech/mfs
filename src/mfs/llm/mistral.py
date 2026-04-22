"""Mistral AI LLM provider.

Requires: ``pip install 'mfs[llm-mistral]'`` or ``uv add 'mfs[llm-mistral]'``
Environment variables:
    MISTRAL_API_KEY — required

Text-only. Mistral has a separate ``pixtral-*`` family for vision; the
default ``mistral-small-latest`` is text-only, so we don't expose
``describe_image`` from this provider.
"""

from __future__ import annotations

import os


class MistralLLM:
    """Mistral chat-completion wrapper."""

    def __init__(
        self,
        model: str = "mistral-small-latest",
        *,
        api_key: str | None = None,
    ) -> None:
        try:
            from mistralai.client import Mistral
        except ImportError as exc:
            raise ImportError(
                "Mistral LLM provider requires mistralai. "
                "Install with: pip install 'mfs[llm-mistral]' "
                "or: uv add 'mfs[llm-mistral]'"
            ) from exc

        self._api_key = api_key or os.environ.get("MISTRAL_API_KEY")
        if not self._api_key:
            raise RuntimeError("MISTRAL_API_KEY is required for the Mistral LLM provider")
        self._client = Mistral(api_key=self._api_key)
        self._model = model

    @property
    def model_name(self) -> str:
        return self._model

    def generate(self, prompt: str, *, system: str | None = None) -> str:
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = self._client.chat.complete(model=self._model, messages=messages)
        choices = getattr(resp, "choices", None) or []
        if not choices:
            return ""
        message = getattr(choices[0], "message", None)
        if message is None:
            return ""
        content = getattr(message, "content", "")
        if isinstance(content, list):
            # Some SDK versions return a list of content blocks
            parts = []
            for block in content:
                text = getattr(block, "text", None)
                if text is None and isinstance(block, dict):
                    text = block.get("text", "")
                if text:
                    parts.append(text)
            return "".join(parts)
        return content or ""
