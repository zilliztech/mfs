"""Google Gemini LLM/VLM provider.

Requires: ``pip install 'mfs[llm-google]'`` or ``uv add 'mfs[llm-google]'``
Environment variables:
    GOOGLE_API_KEY            — required unless using Vertex AI credentials
    GEMINI_API_KEY            — fallback (used by Google AI Studio docs / community tools)
    GOOGLE_GENAI_USE_VERTEXAI — optional, set to "true" to use Vertex AI auth
"""

from __future__ import annotations

import os


def _resolve_api_key(explicit: str | None) -> str | None:
    """Return the first available API key: explicit arg, GOOGLE_API_KEY, GEMINI_API_KEY."""
    if explicit:
        return explicit
    return os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY") or None


class GoogleLLM:
    """Google Generative AI (Gemini) wrapper.

    Implements both :class:`LLMProvider` and :class:`VLMCapable`. Gemini
    1.5+ / 2.0 models accept image inputs natively, so vision is available
    whenever the underlying SDK call succeeds.
    """

    def __init__(
        self,
        model: str = "gemini-2.5-flash",
        *,
        api_key: str | None = None,
    ) -> None:
        try:
            from google import genai
        except ImportError as exc:
            raise ImportError(
                "Google LLM provider requires google-genai. "
                "Install with: pip install 'mfs[llm-google]' "
                "or: uv add 'mfs[llm-google]'"
            ) from exc

        use_vertex_ai = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").strip().lower() == "true"
        client_kwargs: dict = {"vertexai": use_vertex_ai}
        if not use_vertex_ai:
            resolved = _resolve_api_key(api_key)
            if resolved:
                client_kwargs["api_key"] = resolved
        self._client = genai.Client(**client_kwargs)
        self._model = model

    @property
    def model_name(self) -> str:
        return self._model

    def generate(self, prompt: str, *, system: str | None = None) -> str:
        from google.genai import types

        config: dict = {}
        if system:
            config["system_instruction"] = system
        result = self._client.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(**config) if config else None,
        )
        return getattr(result, "text", "") or ""

    def describe_image(self, image_path: str, *, prompt: str | None = None) -> str:
        from google.genai import types

        from .utils import read_image_bytes

        raw, mime = read_image_bytes(image_path)
        prompt_text = prompt or "Describe this image in detail."
        image_part = types.Part.from_bytes(data=raw, mime_type=mime)
        result = self._client.models.generate_content(
            model=self._model,
            contents=[image_part, prompt_text],
        )
        return getattr(result, "text", "") or ""
