"""InfraStack unit tests: construction order / startup sequence / preload path /
shutdown sequence / Engine handle exposure.

Monkeypatches the 8 factories + load_builtin in the infra module with recording
mocks to assert InfraStack's construction and lifecycle sequence. The last case
uses a real Engine(cfg) to verify the 8 handles live on eng.infra (no Engine-level
properties) and that tests monkeypatch them as eng.infra.<handle> = fake.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from mfs_server.config import ServerConfig
from mfs_server.engine import infra as infra_mod
from mfs_server.engine.engine import Engine
from mfs_server.engine.infra import InfraStack


def _cfg(tmp_path):
    cfg = ServerConfig()
    cfg.metadata.backend = "sqlite"
    cfg.metadata.path = str(tmp_path / "meta.db")
    cfg.transformation_cache.backend = "sqlite"
    cfg.transformation_cache.db_path = str(tmp_path / "tx.db")
    cfg.artifact_cache.root = str(tmp_path / "art")
    return cfg


def _factory(log, name, client):
    """Plain ``(cfg) -> client`` factory that records its call name."""

    def _f(*a, **k):
        log.append(name)
        return client

    return _f


def _caching_factory(log, name, client, seen_tx, key):
    """``(cfg, tx_cache) -> client`` factory that records + captures the tx_cache arg."""

    def _f(cfg, tx):
        log.append(name)
        seen_tx[key] = tx
        return client

    return _f


@pytest.fixture
def fake_stack(monkeypatch, tmp_path):
    """InfraStack whose 8 clients + load_builtin are recording mocks.

    Returns ``(stack, log, clients, seen_tx)`` where ``log`` is the ordered call-name list
    and ``clients`` maps the 8 handle names to their mock objects.
    """
    log: list[str] = []
    seen_tx: dict[str, object] = {}

    def rec(name):
        return lambda *a, **k: log.append(name) or None

    meta = MagicMock()
    meta.connect = AsyncMock(side_effect=rec("meta.connect"))
    meta.init_schema = AsyncMock(side_effect=rec("meta.init_schema"))
    meta.close = AsyncMock(side_effect=rec("meta.close"))

    milvus = MagicMock()
    milvus.connect = MagicMock(side_effect=rec("milvus.connect"))
    milvus.ensure_collection = MagicMock(side_effect=rec("milvus.ensure_collection"))
    milvus.close = MagicMock()  # D4: must NOT be called

    artifact_cache = MagicMock()
    artifact_cache.close = MagicMock()  # D4: must NOT be called

    tx_cache = MagicMock()
    tx_cache.connect = AsyncMock(side_effect=rec("tx_cache.connect"))
    tx_cache.close = AsyncMock(side_effect=rec("tx_cache.close"))

    embed = MagicMock()
    embed.should_preload_on_server_start = MagicMock(return_value=False)
    embed.preload_provider = MagicMock()  # called via asyncio.to_thread
    embed.provider_name = "p"
    embed.model = "m"

    converter = MagicMock()
    vlm = MagicMock()
    summary = MagicMock()

    monkeypatch.setattr(
        infra_mod, "make_metadata_store", _factory(log, "make_metadata_store", meta)
    )
    monkeypatch.setattr(infra_mod, "MilvusStore", _factory(log, "MilvusStore", milvus))
    monkeypatch.setattr(
        infra_mod, "make_artifact_cache", _factory(log, "make_artifact_cache", artifact_cache)
    )
    monkeypatch.setattr(
        infra_mod, "make_transformation_cache", _factory(log, "make_transformation_cache", tx_cache)
    )
    monkeypatch.setattr(
        infra_mod,
        "CachingEmbeddingClient",
        _caching_factory(log, "CachingEmbeddingClient", embed, seen_tx, "embed"),
    )
    monkeypatch.setattr(infra_mod, "ConverterClient", _factory(log, "ConverterClient", converter))
    monkeypatch.setattr(
        infra_mod,
        "CachingVlmClient",
        _caching_factory(log, "CachingVlmClient", vlm, seen_tx, "vlm"),
    )
    monkeypatch.setattr(
        infra_mod,
        "CachingSummaryClient",
        _caching_factory(log, "CachingSummaryClient", summary, seen_tx, "summary"),
    )
    monkeypatch.setattr(infra_mod, "load_builtin", _factory(log, "load_builtin", None))

    stack = InfraStack(_cfg(tmp_path))
    clients = {
        "meta": meta,
        "milvus": milvus,
        "artifact_cache": artifact_cache,
        "tx_cache": tx_cache,
        "embed": embed,
        "converter": converter,
        "vlm": vlm,
        "summary": summary,
    }
    return stack, log, clients, seen_tx


async def test_construction_order_and_tx_cache_sharing(fake_stack):
    """8 clients built in dependency order; embed/vlm/summary share the single tx_cache."""
    stack, log, clients, seen_tx = fake_stack
    assert log == [
        "make_metadata_store",
        "MilvusStore",
        "make_artifact_cache",
        "make_transformation_cache",
        "CachingEmbeddingClient",
        "ConverterClient",
        "CachingVlmClient",
        "CachingSummaryClient",
    ]
    # the three caching clients receive the same tx_cache instance InfraStack built
    assert seen_tx["embed"] is clients["tx_cache"]
    assert seen_tx["vlm"] is clients["tx_cache"]
    assert seen_tx["summary"] is clients["tx_cache"]
    # all 8 handles are exposed on the stack
    for name in (
        "meta",
        "milvus",
        "artifact_cache",
        "tx_cache",
        "embed",
        "converter",
        "vlm",
        "summary",
    ):
        assert getattr(stack, name) is clients[name]


async def test_startup_sequence_no_preload(fake_stack):
    """startup calls load_builtin + storage connect/init in the exact original order, no preload."""
    stack, log, clients, _ = fake_stack
    log.clear()
    await stack.startup()  # preload_local_models defaults to False
    assert log == [
        "load_builtin",
        "meta.connect",
        "meta.init_schema",
        "tx_cache.connect",
        "milvus.connect",
        "milvus.ensure_collection",
    ]
    clients["embed"].preload_provider.assert_not_called()


async def test_preload_path_enabled_then_short_circuits(fake_stack):
    """preload_local_models=True fires preload_provider only when should_preload_on_server_start."""
    stack, log, clients, _ = fake_stack

    # should_preload=True -> preload_provider called exactly once (via asyncio.to_thread)
    clients["embed"].should_preload_on_server_start.return_value = True
    log.clear()
    await stack.startup(preload_local_models=True)
    assert clients["embed"].preload_provider.call_count == 1
    assert "milvus.ensure_collection" in log  # milvus ready before preload

    # should_preload=False -> short-circuits even with preload_local_models=True
    clients["embed"].should_preload_on_server_start.return_value = False
    clients["embed"].preload_provider.reset_mock()
    await stack.startup(preload_local_models=True)
    clients["embed"].preload_provider.assert_not_called()


async def test_shutdown_closes_meta_and_tx_cache_only(fake_stack):
    """shutdown closes meta + tx_cache and does NOT close milvus / artifact_cache (D4)."""
    stack, log, clients, _ = fake_stack
    log.clear()
    await stack.shutdown()
    assert log == ["meta.close", "tx_cache.close"]
    clients["milvus"].close.assert_not_called()
    clients["artifact_cache"].close.assert_not_called()


async def test_engine_exposes_handles_via_infra(tmp_path):
    """Real Engine(cfg): the 8 infra handles live on eng.infra, not on Engine.
    Tests monkeypatch them as eng.infra.<handle> = fake (no Engine-level properties)."""
    eng = Engine(_cfg(tmp_path))
    # Engine exposes no handle attributes of its own — they live on eng.infra.
    for name in (
        "meta",
        "milvus",
        "artifact_cache",
        "tx_cache",
        "embed",
        "converter",
        "vlm",
        "summary",
    ):
        assert not hasattr(eng, name)
        assert hasattr(eng.infra, name)

    # monkeypatch contract: tests rebind eng.infra.<handle> with fakes.
    sentinel_milvus = object()
    eng.infra.milvus = sentinel_milvus
    assert eng.infra.milvus is sentinel_milvus

    await eng.infra.shutdown()  # close the real sqlite handles opened at construction
