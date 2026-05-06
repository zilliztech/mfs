"""OpenAI Chat Completions LLM/VLM provider.

Requires: ``pip install mfs-cli`` (openai is included by default)
Environment variables:
    OPENAI_API_KEY   — required
    OPENAI_BASE_URL  — optional, override API base URL
"""

from __future__ import annotations

import os


class OpenAILLM:
    """OpenAI Chat Completions wrapper.

    Implements both :class:`LLMProvider` and :class:`VLMCapable`. Whether
    ``describe_image`` actually works depends on the configured model: only
    vision-capable models (gpt-4o*, gpt-4-turbo, gpt-4-vision*) accept image
    inputs. For other models a clear ``ValueError`` is raised.
    """

    _VISION_MODEL_TOKENS: tuple[str, ...] = ("gpt-4o", "gpt-4-turbo", "gpt-4-vision")

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        *,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        import openai

        kwargs: dict = {}
        effective_base_url = base_url or os.environ.get("OPENAI_BASE_URL")
        if effective_base_url:
            kwargs["base_url"] = effective_base_url
        effective_api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not effective_api_key:
            raise RuntimeError("OPENAI_API_KEY not set and no api_key provided")
        kwargs["api_key"] = effective_api_key

        self._client = openai.OpenAI(**kwargs)
        self._model = model

    @property
    def model_name(self) -> str:
        return self._model

    def _supports_vision(self) -> bool:
        return any(token in self._model for token in self._VISION_MODEL_TOKENS)

    def generate(self, prompt: str, *, system: str | None = None) -> str:
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = self._client.chat.completions.create(
            model=self._model, messages=messages
        )
        return resp.choices[0].message.content or ""

    def describe_image(self, image_path: str, *, prompt: str | None = None) -> str:
        if not self._supports_vision():
            raise ValueError(
                f"Model {self._model!r} does not support vision. "
                f"Use a vision-capable model (gpt-4o, gpt-4o-mini, gpt-4-turbo)."
            )
        from .utils import encode_image_data_url

        data_url = encode_image_data_url(image_path)
        prompt_text = prompt or "Describe this image in detail."
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_text},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
        )
        return resp.choices[0].message.content or ""
