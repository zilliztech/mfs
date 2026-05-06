"""Mistral AI embedding provider.

Requires: ``pip install 'mfs-cli[mistral]'`` or ``uv add 'mfs-cli[mistral]'``
Environment variables:
    MISTRAL_API_KEY — required

Uses the official ``mistralai`` Python SDK. The default model is
``mistral-embed`` (1024-dim, 8K context). Code-heavy corpora may prefer
``codestral-embed``.
"""

from __future__ import annotations

import os

_KNOWN_DIMENSIONS: dict[str, int] = {
    "mistral-embed": 1024,
    "codestral-embed": 1536,
    "codestral-embed-2505": 1536,
}


class MistralEmbedding:
    """Mistral AI embedding provider."""

    _DEFAULT_BATCH_SIZE = 64

    def __init__(
        self,
        model: str = "mistral-embed",
        *,
        batch_size: int = 0,
        api_key: str | None = None,
    ) -> None:
        try:
            from mistralai.client import Mistral
        except ImportError as exc:
            raise ImportError(
                "Mistral embedding provider requires mistralai. "
                "Install with: pip install 'mfs-cli[mistral]' or: uv add 'mfs-cli[mistral]'"
            ) from exc

        self._api_key = api_key or os.environ.get("MISTRAL_API_KEY")
        if not self._api_key:
            raise RuntimeError("MISTRAL_API_KEY is required for the Mistral embedding provider")

        self._client = Mistral(api_key=self._api_key)
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
        resp = self._client.embeddings.create(model=self._model, inputs=texts)
        return [item.embedding for item in resp.data]


def _detect_dimension(client, model: str) -> int:
    """Return the embedding dimension for *model*.

    Uses a lookup table for well-known Mistral models. For unknown models,
    a sync trial embed discovers the dimension.
    """
    if model in _KNOWN_DIMENSIONS:
        return _KNOWN_DIMENSIONS[model]
    trial = client.embeddings.create(model=model, inputs=["dim"])
    return len(trial.data[0].embedding)
