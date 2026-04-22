"""Ollama LLM provider (local models via Ollama server).

Requires: ``pip install 'mfs[llm-ollama]'`` or ``uv add 'mfs[llm-ollama]'``
Environment variables:
    OLLAMA_HOST — optional, default ``http://localhost:11434``

Text-only — vision is intentionally not implemented even though some Ollama
models (llava, llama3.2-vision) accept images. If you need image
descriptions, use the openai/anthropic/google providers.
"""

from __future__ import annotations


class OllamaLLM:
    """Ollama text-generation wrapper."""

    def __init__(
        self,
        model: str = "llama3.2",
    ) -> None:
        try:
            import ollama
        except ImportError as exc:
            raise ImportError(
                "Ollama LLM provider requires ollama. "
                "Install with: pip install 'mfs[llm-ollama]' "
                "or: uv add 'mfs[llm-ollama]'"
            ) from exc

        self._client = ollama.Client()  # reads OLLAMA_HOST
        self._model = model

    @property
    def model_name(self) -> str:
        return self._model

    def generate(self, prompt: str, *, system: str | None = None) -> str:
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = self._client.chat(model=self._model, messages=messages)
        # Ollama returns a dict-like object: resp["message"]["content"]
        message = resp.get("message") if isinstance(resp, dict) else getattr(resp, "message", None)
        if isinstance(message, dict):
            return message.get("content", "") or ""
        return getattr(message, "content", "") or ""
