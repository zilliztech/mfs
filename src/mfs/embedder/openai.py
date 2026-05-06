"""OpenAI embedding provider.

Requires: ``pip install mfs-cli`` (openai is included by default)
Environment variables:
    OPENAI_API_KEY   — required
    OPENAI_BASE_URL  — optional, override API base URL
"""

from __future__ import annotations

import os


class OpenAIEmbedding:
    """OpenAI text-embedding provider."""

    # OpenAI limits total tokens per embedding request to 300K.
    # A conservative batch size keeps us well under that ceiling.
    _DEFAULT_BATCH_SIZE = 128

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        *,
        batch_size: int = 0,
        base_url: str | None = None,
        api_key: str | None = None,
        dimension: int | None = None,
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
        self._dimension = dimension if dimension is not None else _detect_dimension(model, kwargs)
        self._batch_size = batch_size if batch_size > 0 else self._DEFAULT_BATCH_SIZE

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        from .utils import batched_embed

        # OpenAI rejects empty strings; coerce them to a single space.
        payload = [t if t.strip() else " " for t in texts]
        return batched_embed(payload, self._embed_batch, self._batch_size)

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.embeddings.create(
            input=texts, model=self._model, encoding_format="float"
        )
        return [item.embedding for item in resp.data]


_KNOWN_DIMENSIONS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


def _detect_dimension(model: str, client_kwargs: dict) -> int:
    """Return the embedding dimension for *model*.

    Uses a lookup table for well-known OpenAI models. For unknown models
    (e.g. custom models via OPENAI_BASE_URL), a trial embed is performed.
    """
    if model in _KNOWN_DIMENSIONS:
        return _KNOWN_DIMENSIONS[model]
    import openai

    sync_client = openai.OpenAI(**client_kwargs)
    trial = sync_client.embeddings.create(input=["dim"], model=model, encoding_format="float")
    return len(trial.data[0].embedding)
