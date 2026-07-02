"""Registry-level coverage for mfs_server.common.embeddings: every provider name is
wired consistently (registry entry, default model, install hint), and the two
REST-only providers (jina has no SDK; mistral's SDK isn't installed here) fail the
way callers expect instead of a confusing stack trace."""

from __future__ import annotations

import json

import httpx
import pytest

from mfs_server.common.embeddings import DEFAULT_MODELS, get_provider, supported_providers

_EXPECTED_PROVIDERS = {"onnx", "openai", "gemini", "voyage", "jina", "mistral", "ollama", "local"}


def test_registry_lists_every_provider():
    assert set(supported_providers()) == _EXPECTED_PROVIDERS


def test_every_provider_has_a_default_model():
    for name in _EXPECTED_PROVIDERS:
        assert DEFAULT_MODELS.get(name), f"{name} has no default model"


def test_jina_requires_api_key(monkeypatch):
    monkeypatch.delenv("JINA_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="JINA_API_KEY"):
        get_provider("jina")


def test_mistral_missing_sdk_raises_clear_import_error():
    # mistralai is an optional extra, not installed in this test environment —
    # confirms the registry's install-hint path fires instead of a raw ImportError.
    with pytest.raises(ImportError, match="mistral.*uv sync --extra mistral"):
        get_provider("mistral", model="mistral-embed", api_key="fake")


async def test_jina_embed_parses_response(monkeypatch):
    monkeypatch.setenv("JINA_API_KEY", "fake-key")
    provider = get_provider("jina", model="jina-embeddings-v4")

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={"data": [{"embedding": [0.1, 0.2]}, {"embedding": [0.3, 0.4]}]},
        )

    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    vecs = await provider.embed(["hello", "world"])

    assert vecs == [[0.1, 0.2], [0.3, 0.4]]
    assert captured["auth"] == "Bearer fake-key"
    assert captured["json"]["model"] == "jina-embeddings-v4"
    assert captured["json"]["input"] == ["hello", "world"]
    assert captured["json"]["task"] == "retrieval.passage"
