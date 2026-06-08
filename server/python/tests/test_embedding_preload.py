from __future__ import annotations

from mfs_server.common.embedding import CachingEmbeddingClient
from mfs_server.config import ServerConfig


def test_downloadable_local_embedding_provider_is_preloadable(monkeypatch):
    cfg = ServerConfig()
    cfg.embedding.provider = "onnx"
    client = CachingEmbeddingClient(cfg, tx_cache=None)
    calls = 0

    def fake_ensure_provider():
        nonlocal calls
        calls += 1
        return object()

    monkeypatch.setattr(client, "_ensure_provider", fake_ensure_provider)

    assert client.should_preload_on_server_start() is True
    client.preload_provider()
    assert calls == 1


def test_sentence_transformers_embedding_provider_is_preloadable():
    cfg = ServerConfig()
    cfg.embedding.provider = "local"
    client = CachingEmbeddingClient(cfg, tx_cache=None)

    assert client.should_preload_on_server_start() is True


def test_hosted_embedding_provider_is_not_preloaded():
    cfg = ServerConfig()
    cfg.embedding.provider = "openai"
    client = CachingEmbeddingClient(cfg, tx_cache=None)

    assert client.should_preload_on_server_start() is False
