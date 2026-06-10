"""Feishu user-mode auth: lazy refresh, token reuse, needs-reauth, and the
expired-token retry wrapper (_call)."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from mfs_server.connectors.base import ConnectorContext
from mfs_server.connectors.feishu import oauth as feishu_oauth
from mfs_server.connectors.feishu import plugin as feishu_plugin
from mfs_server.connectors.feishu.plugin import FeishuPlugin


class MemoryState:
    async def get(self, key: str) -> Any | None:
        return None

    async def set(self, key: str, value: Any) -> None:
        return None

    async def delete(self, key: str) -> None:
        return None

    async def checkpoint(self) -> None:
        return None


class FakeResp:
    """Minimal stand-in for a lark-oapi response."""

    def __init__(self, ok: bool, code: int = 0):
        self._ok = ok
        self.code = code
        self.msg = "fake"

    def success(self) -> bool:
        return self._ok


def _plugin(cfg: dict) -> FeishuPlugin:
    ctx = ConnectorContext(MemoryState(), "cid", "ns")
    return FeishuPlugin(cfg, None, ctx=ctx)


def _write_oauth(tmp_path, **over) -> str:
    blob = {
        "app_id": "cli_x",
        "app_secret": "secret_x",
        "refresh_token": "refresh_old",
        "region": "feishu",
    }
    blob.update(over)
    p = tmp_path / "oauth.json"
    p.write_text(json.dumps(blob))
    return str(p)


def _stub_lark_client(monkeypatch):
    """Replace lark.Client so connect() builds a dummy client without network."""
    import unittest.mock as mock

    monkeypatch.setattr(feishu_plugin.lark, "Client", mock.MagicMock())


def _fresh_token(**over) -> dict:
    tok = {
        "access_token": "access_new",
        "refresh_token": "refresh_new",
        "expires_in": 7200,
        "refresh_token_expires_in": 604800,
        "scope": "",
    }
    tok.update(over)
    return tok


@pytest.mark.anyio
async def test_connect_reuses_valid_access_token(tmp_path, monkeypatch):
    """A still-valid access_token is reused — refresh is NOT called."""
    path = _write_oauth(tmp_path, access_token="access_valid", access_expires_at=time.time() + 9999)
    called = {"refresh": False}

    def boom(*a, **k):
        called["refresh"] = True
        raise AssertionError("refresh must not be called when token is valid")

    monkeypatch.setattr(feishu_oauth, "refresh_user_token", boom)
    _stub_lark_client(monkeypatch)

    plug = _plugin({"auth": "user", "oauth_state_file": path})
    await plug.connect()

    assert plug._user_token == "access_valid"
    assert called["refresh"] is False


@pytest.mark.anyio
async def test_connect_refreshes_expired_access_token(tmp_path, monkeypatch):
    """An expired access_token triggers a refresh; the new token + rotated refresh_token
    are persisted back to the oauth file."""
    path = _write_oauth(tmp_path, access_token="access_old", access_expires_at=0)
    monkeypatch.setattr(feishu_oauth, "refresh_user_token", lambda *a, **k: _fresh_token())
    _stub_lark_client(monkeypatch)

    plug = _plugin({"auth": "user", "oauth_state_file": path})
    await plug.connect()

    assert plug._user_token == "access_new"
    blob = json.loads(open(path).read())
    assert blob["access_token"] == "access_new"
    assert blob["refresh_token"] == "refresh_new"  # rotated + persisted
    assert blob["access_expires_at"] > time.time()


@pytest.mark.anyio
async def test_connect_legacy_blob_without_access_token_refreshes(tmp_path, monkeypatch):
    """A legacy oauth file (only refresh_token) refreshes to obtain an access_token."""
    path = _write_oauth(tmp_path)  # no access_token at all
    monkeypatch.setattr(feishu_oauth, "refresh_user_token", lambda *a, **k: _fresh_token())
    _stub_lark_client(monkeypatch)

    plug = _plugin({"auth": "user", "oauth_state_file": path})
    await plug.connect()
    assert plug._user_token == "access_new"


@pytest.mark.anyio
async def test_refresh_failure_raises_needs_reauth(tmp_path, monkeypatch):
    """A dead refresh_token surfaces as a needs-reauth error pointing at the auth command."""
    path = _write_oauth(tmp_path, access_token="access_old", access_expires_at=0)

    def fail(*a, **k):
        raise feishu_oauth.OAuthError("invalid_grant", "expired")

    monkeypatch.setattr(feishu_oauth, "refresh_user_token", fail)
    _stub_lark_client(monkeypatch)

    plug = _plugin({"auth": "user", "oauth_state_file": path})
    with pytest.raises(ValueError, match="mfs connector auth"):
        await plug.connect()


@pytest.mark.anyio
async def test_call_retries_once_on_expired_token_code(tmp_path, monkeypatch):
    """_call refreshes and retries when a call returns an expired-token code."""
    path = _write_oauth(tmp_path, access_token="access_old", access_expires_at=time.time() + 9999)
    monkeypatch.setattr(feishu_oauth, "refresh_user_token", lambda *a, **k: _fresh_token())

    plug = _plugin({"auth": "user", "oauth_state_file": path})
    plug._user_token = "access_old"
    from pathlib import Path

    plug._oauth_path = Path(path)

    expired_code = next(iter(feishu_plugin._TOKEN_EXPIRED_CODES))
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return FakeResp(False, expired_code) if calls["n"] == 1 else FakeResp(True)

    resp = await plug._call(fn, "test.call")
    assert resp.success()
    assert calls["n"] == 2  # failed once, refreshed, retried
    assert plug._user_token == "access_new"  # refresh updated the in-memory token


@pytest.mark.anyio
async def test_call_raises_on_non_token_error_without_retry(tmp_path, monkeypatch):
    """A non-token failure is not retried — it raises immediately."""
    path = _write_oauth(tmp_path, access_token="a", access_expires_at=time.time() + 9999)
    plug = _plugin({"auth": "user", "oauth_state_file": path})
    plug._user_token = "a"
    from pathlib import Path

    plug._oauth_path = Path(path)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return FakeResp(False, 12345)  # not a token-expiry code

    with pytest.raises(RuntimeError, match="test.call failed"):
        await plug._call(fn, "test.call")
    assert calls["n"] == 1  # no retry


# ─── wizard device-flow + connector auth ────────────────────────────────────

_LOGIN = "mfs_server.connectors.feishu.auth_login.perform_device_login"


def test_run_feishu_device_login_resolves_secret_and_backfills(tmp_path, monkeypatch):
    from mfs_server.server import connector_wizard as cw

    monkeypatch.setattr(cw, "mfs_home", lambda: tmp_path)
    monkeypatch.setenv("MY_SECRET", "resolved_secret")
    captured: dict = {}

    def fake_login(app_id, app_secret, output, *, region="feishu", scopes=None, **k):
        captured.update(app_id=app_id, app_secret=app_secret, output=output, region=region)
        return True

    monkeypatch.setattr(_LOGIN, fake_login)
    values = {"auth": "user", "app_id": "cli_x", "app_secret": "env:MY_SECRET", "region": "feishu"}
    assert cw.run_feishu_device_login(values, "myalias") is True
    assert captured["app_secret"] == "resolved_secret"  # env: ref resolved for the flow
    assert captured["app_id"] == "cli_x"
    # default oauth_state_file backfilled per-connector
    assert values["oauth_state_file"] == str(tmp_path / "feishu-myalias.oauth.json")
    assert captured["output"] == values["oauth_state_file"]


def test_run_feishu_device_login_tenant_skips(monkeypatch):
    from mfs_server.server import connector_wizard as cw

    def boom(*a, **k):
        raise AssertionError("tenant mode must not authorize")

    monkeypatch.setattr(_LOGIN, boom)
    assert cw.run_feishu_device_login({"auth": "tenant"}, "a") is True


def test_auth_entry_reads_config_and_authorizes(tmp_path, monkeypatch):
    from mfs_server.server import connector_wizard as cw

    monkeypatch.setattr(cw, "mfs_home", lambda: tmp_path)
    monkeypatch.setenv("MY_SECRET", "s3")
    cdir = tmp_path / "connectors"
    cdir.mkdir()
    alias = cw._derive_alias("feishu://ws")
    state = tmp_path / "o.json"
    (cdir / f"{alias}.toml").write_text(
        "# URI: feishu://ws\n"
        'auth = "user"\n'
        'app_id = "cli_x"\n'
        'app_secret = "env:MY_SECRET"\n'
        'region = "feishu"\n'
        f'oauth_state_file = "{state}"\n'
    )
    captured: dict = {}

    def fake_login(app_id, app_secret, output, *, region="feishu", scopes=None, **k):
        captured.update(app_id=app_id, app_secret=app_secret, output=output, region=region)
        return True

    monkeypatch.setattr(_LOGIN, fake_login)
    assert cw.auth_entry(["feishu://ws"]) == 0
    assert captured["app_secret"] == "s3"
    assert captured["output"] == str(state)


def test_auth_entry_missing_config_errors(tmp_path, monkeypatch):
    from mfs_server.server import connector_wizard as cw

    monkeypatch.setattr(cw, "mfs_home", lambda: tmp_path)
    (tmp_path / "connectors").mkdir()
    assert cw.auth_entry(["feishu://nope"]) == 2
