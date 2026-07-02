"""GET /v1/cat?locator=... : the decoded locator must be a JSON object.

A non-dict locator (array, number, string, bool, null) can never satisfy
_locator_matches's `k in locator` / `locator.get(k)` checks -- historically
that either raised an unhandled 500 (list/int/etc.) or, for `null`, silently
fell through to "no locator given" instead of erroring. Both are wrong: the
client asked for a specific record and typo'd the shape, it should get a
clean 400, not a crash or a different answer.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mfs_server.config import ServerConfig
from mfs_server.connectors.base import ObjectConfig, PathStat
from mfs_server.engine.engine import Engine


def _app_client(tmp_path):
    cfg = ServerConfig(home=str(tmp_path), auth_token="expected").resolve_defaults()
    from mfs_server.api.app import create_app

    app = create_app(cfg)
    client = TestClient(app)
    client.headers["Authorization"] = "Bearer expected"
    return client


@pytest.mark.parametrize(
    "raw_locator",
    ['["id","ord_1001"]', "42", "null", "true", '"just a string"'],
)
def test_cat_rejects_non_object_locator(tmp_path, raw_locator: str) -> None:
    client = _app_client(tmp_path)

    resp = client.get("/v1/cat", params={"path": "postgres://db/orders", "locator": raw_locator})

    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "bad_request"
    assert body["detail"] == "invalid locator JSON"


def test_cat_rejects_malformed_locator_json_syntax(tmp_path) -> None:
    client = _app_client(tmp_path)

    resp = client.get(
        "/v1/cat", params={"path": "postgres://db/orders", "locator": "{not valid json"}
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "invalid locator JSON"


# --- engine-level regression coverage: valid-object locators are unaffected ---

_OCFG = ObjectConfig(text_fields=["title"], locator_fields=["id"])


class _FakeConnCtx:
    def object_config_for(self, path):
        return _OCFG


class _FakeStructuredPlugin:
    def __init__(self, records: list[dict]):
        self._records = records
        self.ctx = _FakeConnCtx()
        self.closed = False

    async def stat(self, rel):
        return PathStat(
            path=rel,
            type="file",
            media_type="application/x-collection",
            size_hint=1,
            fingerprint="fp:" + rel,
        )

    def object_kind_of(self, rel):
        return "table_rows"

    def read_records(self, rel, range=None):
        recs = self._records

        async def gen():
            for r in recs:
                yield r

        return gen()

    async def close(self) -> None:
        self.closed = True


async def _build_engine(tmp_path) -> Engine:
    cfg = ServerConfig()
    cfg.metadata.backend = "sqlite"
    cfg.metadata.path = str(tmp_path / "meta.db")
    cfg.transformation_cache.backend = "sqlite"
    cfg.transformation_cache.db_path = str(tmp_path / "tx.db")
    cfg.artifact_cache.root = str(tmp_path / "art")
    eng = Engine(cfg)
    await eng.meta.connect()
    await eng.meta.init_schema()
    return eng


async def test_cat_with_matching_dict_locator_returns_the_record(tmp_path) -> None:
    eng = await _build_engine(tmp_path)
    plugin = _FakeStructuredPlugin([{"id": "ord_1001", "title": "widget"}])

    async def fake_open_path(path: str):
        return "cid", "postgres://db", "/orders", plugin

    eng._open_path = fake_open_path  # type: ignore[method-assign]

    out = await eng.cat("postgres://db/orders", locator={"id": "ord_1001"})

    assert out["locator"] == {"id": "ord_1001"}
    assert "ord_1001" in out["content"]
    await eng.meta.close()


async def test_cat_with_dict_locator_no_match_raises_locator_not_found(tmp_path) -> None:
    eng = await _build_engine(tmp_path)
    plugin = _FakeStructuredPlugin([{"id": "ord_1001", "title": "widget"}])

    async def fake_open_path(path: str):
        return "cid", "postgres://db", "/orders", plugin

    eng._open_path = fake_open_path  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="locator_not_found"):
        await eng.cat("postgres://db/orders", locator={"id": "does_not_exist"})

    await eng.meta.close()
