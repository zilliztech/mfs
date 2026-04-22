"""Embedding providers — protocols, factory, and concrete implementations.

Providers are listed in `_PROVIDERS`. They are imported lazily so that
optional dependencies (e.g. onnxruntime, voyageai) only load when requested.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Minimal interface every embedding backend must satisfy."""

    @property
    def model_name(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


@runtime_checkable
class VisionCapable(Protocol):
    """Additional capability: embed images into the same vector space as text."""

    def embed_images(self, image_paths: list[str]) -> list[list[float]]: ...


# Provider registry: name -> (module_path, class_name)
_PROVIDERS: dict[str, tuple[str, str]] = {
    "openai":  ("mfs.embedder.openai",  "OpenAIEmbedding"),
    "onnx":    ("mfs.embedder.onnx",    "OnnxEmbedding"),
    "google":  ("mfs.embedder.google",  "GoogleEmbedding"),
    "voyage":  ("mfs.embedder.voyage",  "VoyageEmbedding"),
    "jina":    ("mfs.embedder.jina",    "JinaEmbedding"),
    "mistral": ("mfs.embedder.mistral", "MistralEmbedding"),
    "ollama":  ("mfs.embedder.ollama",  "OllamaEmbedding"),
    "local":   ("mfs.embedder.local",   "LocalEmbedding"),
}

DEFAULT_MODELS: dict[str, str] = {
    "openai": "text-embedding-3-small",
    "onnx": "gpahal/bge-m3-onnx-int8",
    "google": "gemini-embedding-001",
    "voyage": "voyage-3-lite",
    "jina": "jina-embeddings-v4",
    "mistral": "mistral-embed",
    "ollama": "nomic-embed-text",
    "local": "all-MiniLM-L6-v2",
}

DEFAULT_DIMENSIONS: dict[str, int] = {
    "openai": 1536,
    "onnx": 1024,
    "google": 768,
    "voyage": 512,
    "jina": 2048,
    "mistral": 1024,
    "ollama": 768,
    "local": 384,
}

_INSTALL_HINTS: dict[str, str] = {
    "openai": "pip install mfs  (or: uv add mfs)",
    "onnx": 'pip install "mfs[onnx]"  (or: uv add "mfs[onnx]")',
    "google": 'pip install "mfs[google]"  (or: uv add "mfs[google]")',
    "voyage": 'pip install "mfs[voyage]"  (or: uv add "mfs[voyage]")',
    "jina": 'pip install "mfs[jina]"  (or: uv add "mfs[jina]")',
    "mistral": 'pip install "mfs[mistral]"  (or: uv add "mfs[mistral]")',
    "ollama": 'pip install "mfs[ollama]"  (or: uv add "mfs[ollama]")',
    "local": 'pip install "mfs[local]"  (or: uv add "mfs[local]")',
}


def get_provider(
    name: str = "openai",
    *,
    model: str | None = None,
    api_key: str | None = None,
    dimension: int | None = None,
    batch_size: int = 0,
    base_url: str | None = None,
) -> EmbeddingProvider:
    """Instantiate an embedding provider by name with lazy import.

    Parameters
    ----------
    name:
        One of the keys in `_PROVIDERS`.
    model:
        Override the provider's default model.
    api_key:
        Override the API key (used by openai/jina/mistral providers).
    dimension:
        Override the embedding dimension (used by the openai provider for
        custom models served via OPENAI_BASE_URL).
    batch_size:
        Maximum number of texts per embedding call. ``0`` means the
        provider's built-in default.
    base_url:
        Override the API base URL (currently only used by the openai provider).
    """
    if name not in _PROVIDERS:
        raise ValueError(
            f"Unknown embedding provider {name!r}. "
            f"Available: {', '.join(sorted(_PROVIDERS))}"
        )

    module_path, class_name = _PROVIDERS[name]
    try:
        import importlib

        mod = importlib.import_module(module_path)
    except ImportError as exc:
        hint = _INSTALL_HINTS.get(name, "")
        raise ImportError(
            f"Embedding provider {name!r} requires extra dependencies. "
            f"Install with: {hint}"
        ) from exc

    cls = getattr(mod, class_name)
    kwargs: dict = {}
    if model is not None:
        kwargs["model"] = model
    if batch_size > 0:
        kwargs["batch_size"] = batch_size
    if name == "openai":
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        if dimension is not None:
            kwargs["dimension"] = dimension
    elif name in ("jina", "mistral", "google") and api_key:
        kwargs["api_key"] = api_key
    return cls(**kwargs)


__all__ = [
    "DEFAULT_DIMENSIONS",
    "DEFAULT_MODELS",
    "EmbeddingProvider",
    "VisionCapable",
    "get_provider",
]
