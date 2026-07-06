"""Google Gemini LLM provider (text + vision).

Requires `uv sync --extra gemini`. Reads GOOGLE_API_KEY from env (or
GOOGLE_GENAI_USE_VERTEXAI=true for Vertex AI auth).
"""

from __future__ import annotations


class GeminiLlm:
    _DEFAULT_MODEL = "gemini-2.0-flash"

    def __init__(self, **_kwargs: object) -> None:
        # ``_kwargs`` lets the registry's get_provider(**kwargs) forward
        # base_url/api_key uniformly; this provider ignores them and reads
        # GOOGLE_API_KEY from env.
        from google import genai

        self._client = genai.Client()  # GOOGLE_API_KEY from env
        # genai.types — imported lazily inside methods to keep startup cheap
        self._types_module = None

    def _types(self):
        if self._types_module is None:
            from google.genai import types

            self._types_module = types
        return self._types_module

    async def chat(
        self,
        prompt: str,
        *,
        model: str | None = None,
        max_tokens: int = 800,
        temperature: float = 0.3,
    ) -> str:
        types = self._types()
        resp = await self._client.aio.models.generate_content(
            model=model or self._DEFAULT_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                max_output_tokens=max_tokens,
                temperature=temperature,
            ),
        )
        return resp.text or ""

    async def vision(
        self,
        prompt: str,
        image_bytes: bytes,
        mime: str,
        *,
        model: str | None = None,
        max_tokens: int = 800,
    ) -> str:
        types = self._types()
        image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime)
        resp = await self._client.aio.models.generate_content(
            model=model or self._DEFAULT_MODEL,
            contents=[prompt, image_part],
            config=types.GenerateContentConfig(max_output_tokens=max_tokens),
        )
        return resp.text or ""
