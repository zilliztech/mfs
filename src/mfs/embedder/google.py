"""Google (Gemini) embedding provider.

Requires: ``pip install 'mfs[google]'`` or ``uv add 'mfs[google]'``
Environment variables:
    GOOGLE_API_KEY — required unless using Vertex AI credentials
    GEMINI_API_KEY — fallback (used by Google AI Studio docs / community tools)
    GOOGLE_GENAI_USE_VERTEXAI — optional, set to "true" to use Vertex AI auth
"""

from __future__ import annotations

import os

# gemini-embedding-001 natively outputs 3072, but 768 is the recommended
# default (Matryoshka truncation, saves storage).
_KNOWN_DIMENSIONS: dict[str, int] = {
    "gemini-embedding-001": 768,
    "gemini-embedding-2-preview": 768,
    "text-embedding-005": 768,
    "text-embedding-004": 768,
}


def _resolve_api_key(explicit: str | None) -> str | None:
    """Return the first available API key: explicit arg, GOOGLE_API_KEY, GEMINI_API_KEY."""
    if explicit:
        return explicit
    return os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY") or None


class GoogleEmbedding:
    """Google Generative AI embedding provider."""

    _DEFAULT_BATCH_SIZE = 100

    def __init__(
        self,
        model: str = "gemini-embedding-001",
        *,
        api_key: str | None = None,
        batch_size: int = 0,
    ) -> None:
        try:
            from google import genai
        except ImportError as exc:
            raise ImportError(
                "Google embedding provider requires google-genai. "
                "Install with: pip install 'mfs[google]' or: uv add 'mfs[google]'"
            ) from exc

        use_vertex_ai = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").strip().lower() == "true"
        client_kwargs: dict = {"vertexai": use_vertex_ai}
        if not use_vertex_ai:
            resolved = _resolve_api_key(api_key)
            if resolved:
                client_kwargs["api_key"] = resolved
        self._client = genai.Client(**client_kwargs)
        self._model = model
        self._dimension = _detect_dimension(self._client, model)
        self._batch_size = batch_size if batch_size > 0 else self._DEFAULT_BATCH_SIZE

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        from .utils import batched_embed

        return batched_embed(texts, self._embed_batch, self._batch_size)

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        from google.genai import types

        result = self._client.models.embed_content(
            model=self._model,
            contents=texts,
            config=types.EmbedContentConfig(output_dimensionality=self._dimension),
        )
        return [e.values for e in result.embeddings]


def _detect_dimension(client, model: str) -> int:
    """Return the embedding dimension for *model*.

    Uses a lookup table for well-known models. For unknown models, a trial
    embed discovers the native dimension.
    """
    if model in _KNOWN_DIMENSIONS:
        return _KNOWN_DIMENSIONS[model]
    result = client.models.embed_content(model=model, contents=["dim"])
    return len(result.embeddings[0].values)
