"""LLM provider registry for text + vision tasks.

Used by:
  - common/summary.py for directory_summary / schema_summary chunks
  - common/vlm.py for image-description chunks

Each provider exposes the same protocol:

    class XxxLlm:
        async def chat(self, prompt: str, *, model: str | None = None,
                       max_tokens: int = 800, temperature: float = 0.3) -> str
        async def vision(self, prompt: str, image_bytes: bytes, mime: str, *,
                         model: str | None = None, max_tokens: int = 800) -> str

`get_provider(name)` lazy-imports the SDK; ImportError comes with an
install hint identifying the right extras_require.
"""

from __future__ import annotations

from typing import Any

_PROVIDERS: dict[str, tuple[str, str]] = {
    "openai": ("mfs_server.common.llm.openai", "OpenAILlm"),
    "anthropic": ("mfs_server.common.llm.anthropic", "AnthropicLlm"),
    "gemini": ("mfs_server.common.llm.gemini", "GeminiLlm"),
}


# Recommended default model per provider for general text + vision tasks.
DEFAULT_TEXT_MODELS: dict[str, str] = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-4-5-20250929",
    "gemini": "gemini-2.0-flash",
}

# Default model for image-description (vision) tasks. Most providers use
# the same multimodal model for text + vision; gemini-2.0-flash and
# claude-sonnet-4-5 accept images natively.
DEFAULT_VISION_MODELS: dict[str, str] = DEFAULT_TEXT_MODELS.copy()


_INSTALL_HINTS: dict[str, str] = {
    "openai": "uv sync  (core dep)",
    "anthropic": "uv sync --extra anthropic",
    "gemini": "uv sync --extra gemini",
}


def supported_providers() -> list[str]:
    return list(_PROVIDERS.keys())


def get_provider(name: str) -> Any:
    """Instantiate the requested LLM provider lazily."""
    if name not in _PROVIDERS:
        raise ValueError(
            f"unknown LLM provider {name!r}; supported: {', '.join(supported_providers())}"
        )
    module_path, class_name = _PROVIDERS[name]
    hint = _INSTALL_HINTS.get(name, f"install the {name} SDK")
    try:
        module = __import__(module_path, fromlist=[class_name])
        cls = getattr(module, class_name)
        return cls()
    except ImportError as e:
        raise ImportError(
            f"LLM provider {name!r} is not installed: {e}.  Install with: {hint}"
        ) from e


__all__ = [
    "DEFAULT_TEXT_MODELS",
    "DEFAULT_VISION_MODELS",
    "get_provider",
    "supported_providers",
]
