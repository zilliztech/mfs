"""LLM/VLM providers — protocols, factory, and concrete implementations.

Providers are listed in `_PROVIDERS`. They are imported lazily so that
optional dependencies (e.g. anthropic, google-genai, ollama) only load
when requested.

Two protocols are exposed:

- ``LLMProvider`` — text-in, text-out generation (every provider implements this).
- ``VLMCapable``  — additionally describes images. Only providers whose
  underlying model supports vision implement this.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    """Minimal interface every text-generation backend must satisfy."""

    @property
    def model_name(self) -> str: ...

    def generate(self, prompt: str, *, system: str | None = None) -> str: ...


@runtime_checkable
class VLMCapable(Protocol):
    """Additional capability: produce a textual description of an image."""

    def describe_image(self, image_path: str, *, prompt: str | None = None) -> str: ...


# Provider registry: name -> (module_path, class_name)
_PROVIDERS: dict[str, tuple[str, str]] = {
    "openai":    ("mfs.llm.openai",    "OpenAILLM"),
    "anthropic": ("mfs.llm.anthropic", "AnthropicLLM"),
    "google":    ("mfs.llm.google",    "GoogleLLM"),
    "ollama":    ("mfs.llm.ollama",    "OllamaLLM"),
    "mistral":   ("mfs.llm.mistral",   "MistralLLM"),
}

DEFAULT_MODELS: dict[str, str] = {
    "openai":    "gpt-4o-mini",         # cheap + supports vision
    "anthropic": "claude-3-5-haiku-latest",
    "google":    "gemini-2.5-flash",
    "ollama":    "llama3.2",
    "mistral":   "mistral-small-latest",
}

_INSTALL_HINTS: dict[str, str] = {
    "openai":    "pip install mfs-cli  (or: uv add mfs-cli)",
    "anthropic": 'pip install "mfs-cli[llm-anthropic]"  (or: uv add "mfs-cli[llm-anthropic]")',
    "google":    'pip install "mfs-cli[llm-google]"  (or: uv add "mfs-cli[llm-google]")',
    "ollama":    'pip install "mfs-cli[llm-ollama]"  (or: uv add "mfs-cli[llm-ollama]")',
    "mistral":   'pip install "mfs-cli[llm-mistral]"  (or: uv add "mfs-cli[llm-mistral]")',
}


def get_provider(
    name: str = "openai",
    *,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> LLMProvider:
    """Instantiate an LLM provider by name with lazy import.

    Parameters
    ----------
    name:
        One of the keys in ``_PROVIDERS``.
    model:
        Override the provider's default model. If ``None``, uses
        ``DEFAULT_MODELS[name]``.
    api_key:
        Override the API key (used by openai/anthropic/mistral providers).
    base_url:
        Override the API base URL (currently used by the openai provider).
    """
    if name not in _PROVIDERS:
        raise ValueError(
            f"Unknown LLM provider {name!r}. "
            f"Available: {', '.join(sorted(_PROVIDERS))}"
        )

    module_path, class_name = _PROVIDERS[name]
    try:
        import importlib

        mod = importlib.import_module(module_path)
    except ImportError as exc:
        hint = _INSTALL_HINTS.get(name, "")
        raise ImportError(
            f"LLM provider {name!r} requires extra dependencies. "
            f"Install with: {hint}"
        ) from exc

    cls = getattr(mod, class_name)
    effective_model = model or DEFAULT_MODELS.get(name, "")
    kwargs: dict = {}
    if effective_model:
        kwargs["model"] = effective_model
    if name == "openai":
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
    elif name in ("anthropic", "mistral") and api_key:
        kwargs["api_key"] = api_key
    elif name == "google" and api_key:
        kwargs["api_key"] = api_key
    return cls(**kwargs)


__all__ = [
    "DEFAULT_MODELS",
    "LLMProvider",
    "VLMCapable",
    "get_provider",
]
