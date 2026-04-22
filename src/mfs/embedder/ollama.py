"""Ollama embedding provider (local models via Ollama server).

Requires: ``pip install 'mfs[ollama]'`` or ``uv add 'mfs[ollama]'``
Environment variables:
    OLLAMA_HOST — optional, default http://localhost:11434
"""

from __future__ import annotations


class OllamaEmbedding:
    """Ollama embedding provider."""

    _DEFAULT_BATCH_SIZE = 512

    def __init__(
        self,
        model: str = "nomic-embed-text",
        *,
        batch_size: int = 0,
    ) -> None:
        try:
            import ollama
        except ImportError as exc:
            raise ImportError(
                "Ollama embedding provider requires ollama. "
                "Install with: pip install 'mfs[ollama]' or: uv add 'mfs[ollama]'"
            ) from exc

        self._client = ollama.Client()  # reads OLLAMA_HOST
        self._model = model
        trial = self._client.embed(model=model, input=["dim"])
        self._dimension = len(trial["embeddings"][0])
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
        result = self._client.embed(model=self._model, input=texts)
        return result["embeddings"]
