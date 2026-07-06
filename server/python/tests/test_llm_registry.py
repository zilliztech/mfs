"""Registry-level coverage for mfs_server.common.llm: every provider is wired
consistently (registry entry, default model, install hint), the
``openai_compatible`` provider resolves ``env:``/``file:`` credential refs and
defaults to a placeholder api_key rather than borrowing OPENAI_API_KEY, and the
cloud providers tolerate the registry's uniform ``**kwargs`` forwarding."""

from __future__ import annotations

import inspect

import pytest

from mfs_server.common.llm import (
    DEFAULT_TEXT_MODELS,
    DEFAULT_VISION_MODELS,
    get_provider,
    supported_providers,
)

_EXPECTED_PROVIDERS = {"openai", "openai_compatible", "anthropic", "gemini"}


def test_registry_lists_every_provider():
    assert set(supported_providers()) == _EXPECTED_PROVIDERS


def test_openai_compatible_has_no_default_model():
    # No default — model is endpoint-specific and must be set in toml.
    assert DEFAULT_TEXT_MODELS.get("openai_compatible") == ""
    assert DEFAULT_VISION_MODELS.get("openai_compatible") == ""


def test_openai_compatible_requires_base_url(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    with pytest.raises(ValueError, match="base_url"):
        get_provider("openai_compatible")


def test_openai_compatible_constructs_with_base_url(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    p = get_provider("openai_compatible", base_url="https://api.deepseek.com/v1")
    assert "deepseek.com" in str(p._client.base_url)


def test_openai_compatible_resolves_env_ref(monkeypatch):
    monkeypatch.setenv("MY_COMPAT_KEY", "secret-from-env")
    p = get_provider(
        "openai_compatible",
        base_url="http://localhost:8000/v1",
        api_key="env:MY_COMPAT_KEY",
    )
    assert p._client.api_key == "secret-from-env"


def test_openai_compatible_resolves_file_ref(tmp_path):
    key_file = tmp_path / "key.txt"
    key_file.write_text("secret-from-file\n")
    p = get_provider(
        "openai_compatible",
        base_url="http://localhost:8000/v1",
        api_key=f"file:{key_file}",
    )
    assert p._client.api_key == "secret-from-file"


def test_openai_compatible_plaintext_api_key_passes_through(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    p = get_provider(
        "openai_compatible",
        base_url="http://localhost:8000/v1",
        api_key="sk-plaintext",
    )
    assert p._client.api_key == "sk-plaintext"


def test_openai_compatible_no_api_key_uses_placeholder_not_env(monkeypatch):
    # Without an explicit api_key the provider must NOT fall back to
    # OPENAI_API_KEY (which would silently point a custom endpoint at OpenAI's
    # cloud credentials) — it uses a placeholder instead.
    monkeypatch.setenv("OPENAI_API_KEY", "should-not-be-used")
    p = get_provider("openai_compatible", base_url="http://localhost:8000/v1")
    assert p._client.api_key != "should-not-be-used"
    assert p._client.api_key  # non-empty placeholder


def test_openai_compatible_env_ref_missing_var_raises(monkeypatch):
    monkeypatch.delenv("DEFINITELY_UNSET_VAR_XYZ", raising=False)
    with pytest.raises(ValueError, match="env var.*not set"):
        get_provider(
            "openai_compatible",
            base_url="http://localhost:8000/v1",
            api_key="env:DEFINITELY_UNSET_VAR_XYZ",
        )


def test_cloud_providers_accept_arbitrary_kwargs():
    # anthropic / gemini must tolerate the registry's uniform **kwargs
    # forwarding (base_url/api_key) even though they read creds from env.
    from mfs_server.common.llm.anthropic import AnthropicLlm
    from mfs_server.common.llm.gemini import GeminiLlm

    for cls in (AnthropicLlm, GeminiLlm):
        sig = inspect.signature(cls.__init__)
        assert any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()), (
            f"{cls.__name__}.__init__ must accept **kwargs"
        )
