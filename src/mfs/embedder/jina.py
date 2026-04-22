"""Jina AI embedding provider.

Requires: ``pip install 'mfs[jina]'`` or ``uv add 'mfs[jina]'``
Environment variables:
    JINA_API_KEY — required

Jina does not publish a dedicated Python SDK for its embedding REST API, so
this provider talks to ``https://api.jina.ai/v1/embeddings`` directly via
httpx. The default model is ``jina-embeddings-v4`` (2048-dim, Matryoshka-
truncatable between 256 and 2048).

The ``task`` parameter activates a task-specific LoRA adapter. We default to
``retrieval.passage`` — the common case for indexing a corpus. Override via
the constructor for query- or code-specific behavior.
"""

from __future__ import annotations

import os

_API_URL = "https://api.jina.ai/v1/embeddings"

# Native output dimensions for well-known Jina models. v4 additionally
# supports Matryoshka truncation between 256 and 2048.
_KNOWN_DIMENSIONS: dict[str, int] = {
    "jina-embeddings-v4": 2048,
    "jina-embeddings-v3": 1024,
    "jina-embeddings-v2-base-en": 768,
    "jina-embeddings-v2-base-code": 768,
}


class JinaEmbedding:
    """Jina AI embedding provider (REST)."""

    _DEFAULT_BATCH_SIZE = 128
    _TIMEOUT_SECONDS = 60.0

    def __init__(
        self,
        model: str = "jina-embeddings-v4",
        *,
        batch_size: int = 0,
        task: str = "retrieval.passage",
        dimensions: int | None = None,
        api_key: str | None = None,
    ) -> None:
        import httpx

        self._api_key = api_key or os.environ.get("JINA_API_KEY")
        if not self._api_key:
            raise RuntimeError("JINA_API_KEY is required for the Jina embedding provider")

        self._model = model
        self._task = task
        self._dimensions = dimensions if dimensions is not None else _KNOWN_DIMENSIONS.get(model, 2048)
        self._batch_size = batch_size if batch_size > 0 else self._DEFAULT_BATCH_SIZE
        self._client = httpx.Client(timeout=self._TIMEOUT_SECONDS)

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        from .utils import batched_embed

        return batched_embed(texts, self._embed_batch, self._batch_size)

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        body: dict = {
            "model": self._model,
            "input": texts,
        }
        if self._task:
            body["task"] = self._task
        if self._dimensions:
            body["dimensions"] = self._dimensions

        resp = self._client.post(
            _API_URL,
            json=body,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        return [item["embedding"] for item in payload["data"]]
