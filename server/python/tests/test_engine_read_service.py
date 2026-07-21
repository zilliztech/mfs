"""Independent unit tests for ReadService + text_views: constructed without an
Engine, without real Milvus/meta, injecting fakes. Covers the pure helpers and
the locator pair (open_path / match_connector) that ReadService owns directly
(D1: no Engine back-reference).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from mfs_server.config import ServerConfig
from mfs_server.engine.components.reads import ReadService
from mfs_server.engine.components.reads.text_views import _density_view, _locator_matches


# --- pure helpers (text_views) ---


def test_density_view_peek_markdown_headings():
    text = "# Title\n\nsome prose\n## Sub\nmore"
    assert _density_view(text, ".md", "peek") == "# Title\n## Sub"


def test_density_view_skim_includes_first_prose_line():
    out = _density_view("# Title\n\nfirst prose line\nsecond", ".md", "skim")
    assert "# Title" in out
    assert "first prose line" in out


def test_density_view_code_symbols():
    out = _density_view("def foo():\n    pass\nclass Bar:\n    pass\nrandom", ".py", "peek")
    assert "def foo" in out
    assert "class Bar" in out


def test_density_view_fallback_first_15_lines():
    text = "\n".join(f"line {i}" for i in range(20))
    assert _density_view(text, ".txt", "peek") == "\n".join(f"line {i}" for i in range(15))


class _OCFG:
    locator_fields = ["id"]


def test_locator_matches_dict_key():
    rec = {"id": "ord_1", "name": "x"}
    assert _locator_matches(rec, _OCFG(), 0, {"id": "ord_1"}) is True
    assert _locator_matches(rec, _OCFG(), 0, {"id": "other"}) is False


def test_locator_matches_row():
    assert _locator_matches({}, _OCFG(), 3, {"_row": 3}) is True
    assert _locator_matches({}, _OCFG(), 3, {"_row": 1}) is False


def test_locator_matches_lines_key_ignored():
    # "lines" is framework-reserved, never a structured PK - must not match record #0.
    assert _locator_matches({"id": "ord_1"}, _OCFG(), 0, {"lines": [1, 2]}) is False


def test_locator_matches_empty_locator_no_match():
    # empty locator must not silently return record #0 (the all([]) == True trap).
    assert _locator_matches({"id": "x"}, _OCFG(), 0, {}) is False


# --- ReadService construction + locator pair (fakes, no Engine/Milvus) ---


class _FakeFactory:
    def __init__(self):
        self.open_path_calls = []
        self.resolve_target_calls = []

    async def open_path(self, rows, path):
        self.open_path_calls.append((rows, path))
        return SimpleNamespace(
            cid="cid-fake",
            connector_uri="postgres://db",
            relpath="/orders",
            built=SimpleNamespace(plugin="PLUGIN"),
        )

    def resolve_target(self, target):
        self.resolve_target_calls.append(target)
        return SimpleNamespace(
            connector_uri="postgres://db", ctype="postgres", scheme="postgres", config={}
        )


class _FakeObjects:
    def __init__(self, rows=None, has_any=False):
        self._rows = rows or []
        self._has_any = has_any

    async def list_connectors_all(self):
        return self._rows

    async def has_any_connector(self):
        return self._has_any

    async def has_connector_uri(self, uri):
        return any(r["root_uri"] == uri for r in self._rows)


class _FakeArtifacts:
    async def read_artifact(self, ns, uri, kind):
        return None

    async def converted_md_stale(self, cid, uri, fp):
        return False


def _build_reads(factory=None, objects=None):
    cfg = ServerConfig()
    return ReadService(
        cfg,
        SimpleNamespace(),
        factory or _FakeFactory(),
        objects or _FakeObjects(),
        _FakeArtifacts(),
    )


async def test_open_path_delegates_to_factory():
    factory = _FakeFactory()
    objs = _FakeObjects(rows=[{"root_uri": "postgres://db"}])
    reads = _build_reads(factory=factory, objects=objs)
    out = await reads.open_path("postgres://db/orders")
    assert out == ("cid-fake", "postgres://db", "/orders", "PLUGIN")
    assert factory.open_path_calls == [([{"root_uri": "postgres://db"}], "postgres://db/orders")]


async def test_match_connector_uses_locator():
    rows = [{"root_uri": "postgres://db", "id": "c1"}]
    reads = _build_reads(objects=_FakeObjects(rows=rows))
    out = await reads.match_connector("postgres://db/orders")
    assert out is not None
    row, rel = out
    assert row["root_uri"] == "postgres://db"


async def test_match_connector_none_when_no_rows():
    reads = _build_reads(objects=_FakeObjects(rows=[]))
    assert await reads.match_connector("postgres://db/orders") is None


async def test_search_empty_namespace_short_circuits():
    reads = _build_reads(objects=_FakeObjects(has_any=False))
    assert await reads.search("query") == []


async def test_search_top_k_too_large():
    reads = _build_reads(objects=_FakeObjects(has_any=True))
    with pytest.raises(ValueError, match="top_k_too_large"):
        await reads.search("q", top_k=10**9)


async def test_resolve_connector_uri_matches_registered():
    rows = [{"root_uri": "postgres://db", "id": "c1"}]
    reads = _build_reads(objects=_FakeObjects(rows=rows))
    curi, prefix = await reads.resolve_connector_uri("postgres://db/orders")
    assert curi == "postgres://db"
    assert prefix == "postgres://db/orders"


async def test_resolve_connector_uri_root_rel_none():
    rows = [{"root_uri": "postgres://db", "id": "c1"}]
    reads = _build_reads(objects=_FakeObjects(rows=rows))
    curi, prefix = await reads.resolve_connector_uri("postgres://db")
    assert curi == "postgres://db"
    assert prefix is None


async def test_resolve_connector_uri_fallback_to_factory():
    factory = _FakeFactory()
    reads = _build_reads(factory=factory, objects=_FakeObjects(rows=[]))
    curi, prefix = await reads.resolve_connector_uri("postgres://other")
    assert curi == "postgres://db"
    assert prefix is None
    assert factory.resolve_target_calls == ["postgres://other"]
