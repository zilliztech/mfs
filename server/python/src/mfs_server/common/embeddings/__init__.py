"""Embedding provider registry.

Each provider lives in its own module exposing a class with the shape:

    class XxxEmbedding:
        @property
        def model_name(self) -> str: ...
        @property
        def dimension(self) -> int: ...
        async def embed(self, texts: list[str]) -> list[list[float]]: ...

`get_provider(name, model, **kwargs)` picks the right backend lazily — the
heavy imports (sentence-transformers, onnxruntime, voyageai, ...) happen
only when the provider is actually used.
"""

from __future__ import annotations

from typing import Any

# (module path, class name) per provider — lazy-imported.
_PROVIDERS: dict[str, tuple[str, str]] = {
    "openai": ("mfs_server.common.embeddings.openai", "OpenAIEmbedding"),
    "onnx": ("mfs_server.common.embeddings.onnx", "OnnxEmbedding"),
    "gemini": ("mfs_server.common.embeddings.gemini", "GeminiEmbedding"),
    "voyage": ("mfs_server.common.embeddings.voyage", "VoyageEmbedding"),
    "ollama": ("mfs_server.common.embeddings.ollama", "OllamaEmbedding"),
    "local": ("mfs_server.common.embeddings.local", "LocalEmbedding"),
}


# Recommended default model per provider, used by the setup wizard.
DEFAULT_MODELS: dict[str, str] = {
    "openai": "text-embedding-3-small",
    "onnx": "gpahal/bge-m3-onnx-int8",
    "gemini": "gemini-embedding-001",
    "voyage": "voyage-3-lite",
    "ollama": "nomic-embed-text",
    "local": "all-MiniLM-L6-v2",
}


# Per-provider extras_require hint shown when import fails.
_INSTALL_HINTS: dict[str, str] = {
    "openai": "uv sync  (core dep)",
    "onnx": "uv sync  (core dep)",
    "gemini": "uv sync --extra gemini",
    "voyage": "uv sync --extra voyage",
    "ollama": "uv sync --extra ollama",
    "local": "uv sync --extra local",
}


def supported_providers() -> list[str]:
    return list(_PROVIDERS.keys())


def get_provider(name: str, model: str = "", **kwargs: Any) -> Any:
    """Instantiate the requested embedding provider.

    Raises a clear ImportError telling the user how to install the missing
    dependency when the provider's SDK isn't present. Each provider's SDK
    is imported lazily inside its class __init__, so we catch ImportError
    both at module load and at instantiation.
    """
    if name not in _PROVIDERS:
        raise ValueError(
            f"unknown embedding provider {name!r}; supported: {', '.join(supported_providers())}"
        )
    module_path, class_name = _PROVIDERS[name]
    hint = _INSTALL_HINTS.get(name, f"install the {name} SDK")
    try:
        module = __import__(module_path, fromlist=[class_name])
        cls = getattr(module, class_name)
        effective_model = model or DEFAULT_MODELS.get(name, "")
        if effective_model:
            return cls(model=effective_model, **kwargs)
        return cls(**kwargs)
    except ImportError as e:
        raise ImportError(
            f"embedding provider {name!r} is not installed: {e}.  Install with: {hint}"
        ) from e


__all__ = ["DEFAULT_MODELS", "get_provider", "supported_providers"]
