"""OAuth 2.0 Device Authorization Grant (RFC 8628) helpers for Feishu / Lark.

Used by the feishu connector's `auth = "user"` mode and by the CLI `auth_login`
entry point. Talks to two endpoints directly via httpx; lark-oapi does not (as
of 0.5.x) expose Device Flow in its high-level service surface.

Endpoint URLs and request shapes verified against larksuite/cli's Go
implementation (internal/auth/device_flow.go + paths.go).

Issued tokens:
  access_token  - 2h lifetime; pass as user_access_token to API calls.
  refresh_token - 30d lifetime; persist; exchange for a fresh access_token
                  each time the connector starts up.
"""

from __future__ import annotations

import base64
import time
from typing import Optional

import httpx

# Feishu (国内) vs Lark (海外). The two cloud regions share the same API surface
# (path, request/response shapes, scopes) but use distinct host pairs. Region is
# selected via the `region` config field on the connector / `--region` flag on
# auth_login. Default = "feishu".
_REGIONS = {
    "feishu": {"accounts": "https://accounts.feishu.cn", "open": "https://open.feishu.cn"},
    "lark": {"accounts": "https://accounts.larksuite.com", "open": "https://open.larksuite.com"},
}

_PATH_DEVICE_AUTH = "/oauth/v1/device_authorization"
_PATH_TOKEN_V2 = "/open-apis/authen/v2/oauth/token"


def endpoints(region: str = "feishu") -> dict:
    """Resolve `region` -> {accounts, open} host URLs. Raises on unknown region."""
    if region not in _REGIONS:
        raise ValueError(
            f"feishu connector: unknown region {region!r}; expected one of {sorted(_REGIONS)}"
        )
    return _REGIONS[region]


# Default scope set covering the read paths the feishu connector uses today.
#
# Naming gotcha: Feishu separates BOT (tenant) scopes from USER-OAuth scopes.
# For message reading via user_access_token, the correct suffix is `:get_as_user`,
# NOT `:readonly`. Verified by reading larksuite/cli source
# (shortcuts/im/im_chat_messages_list.go), which splits its declarations into
# `BotScopes` (`im:message.group_msg`) and `UserScopes` (`im:message.group_msg:get_as_user`).
# The `:readonly` variant exists too but applies to bot/tenant tokens — Feishu
# silently drops it from a user-OAuth grant, leaving the user unable to read messages.
#
# `offline_access` is REQUIRED to receive a refresh_token (without it, only a 2h
# access_token comes back and you have to redo the device flow every time).
DEFAULT_SCOPES = [
    "offline_access",
    "im:chat:readonly",
    "im:message.group_msg:get_as_user",  # group messages, user-OAuth variant
    "im:message.p2p_msg:get_as_user",  # p2p messages, user-OAuth variant
    "drive:drive:readonly",
    "docx:document:readonly",
    "contact:user.id:readonly",
]


class OAuthError(RuntimeError):
    """Raised on a non-pending error from a Feishu OAuth endpoint."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code, self.message = code, message


def _basic_auth(app_id: str, app_secret: str) -> str:
    raw = f"{app_id}:{app_secret}".encode()
    return "Basic " + base64.standard_b64encode(raw).decode()


def request_device_authorization(
    app_id: str, app_secret: str, scopes: Optional[list[str]] = None, region: str = "feishu"
) -> dict:
    """Step 1 of Device Flow: get a device_code + user_code + verification URL.

    Returns a dict with: device_code, user_code, verification_uri,
    verification_uri_complete, expires_in (seconds), interval (poll spacing).
    Caller shows verification_uri_complete to the user and starts polling.
    """
    scope_list = list(scopes) if scopes is not None else list(DEFAULT_SCOPES)
    if "offline_access" not in scope_list:
        scope_list.append("offline_access")
    body = {"client_id": app_id, "scope": " ".join(scope_list)}
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": _basic_auth(app_id, app_secret),
    }
    hosts = endpoints(region)
    r = httpx.post(hosts["accounts"] + _PATH_DEVICE_AUTH, data=body, headers=headers, timeout=20)
    data = r.json()
    if r.status_code >= 400 or "error" in data:
        raise OAuthError(
            data.get("error", f"http_{r.status_code}"),
            data.get("error_description") or data.get("error") or "device_authorization failed",
        )
    return {
        "device_code": data["device_code"],
        "user_code": data["user_code"],
        "verification_uri": data.get("verification_uri", ""),
        "verification_uri_complete": data.get("verification_uri_complete")
        or data.get("verification_uri", ""),
        "expires_in": int(data.get("expires_in", 240)),
        "interval": int(data.get("interval", 5)),
    }


def poll_for_device_token(
    app_id: str,
    app_secret: str,
    device_code: str,
    interval: int = 5,
    expires_in: int = 240,
    region: str = "feishu",
) -> dict:
    """Step 2 of Device Flow: poll until the user approves in the browser.

    Returns a dict with: access_token, refresh_token, expires_in, scope.
    Raises OAuthError on terminal errors (expired_token / access_denied / etc.).
    """
    deadline = time.time() + expires_in
    poll_interval = max(1, interval)
    hosts = endpoints(region)
    while time.time() < deadline:
        time.sleep(poll_interval)
        body = {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device_code,
            "client_id": app_id,
            "client_secret": app_secret,
        }
        r = httpx.post(
            hosts["open"] + _PATH_TOKEN_V2,
            data=body,
            timeout=20,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        data = r.json()
        # RFC 8628 standard pending errors — keep polling.
        err = data.get("error")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            poll_interval += 5
            continue
        if err in ("expired_token", "access_denied", "invalid_grant", "invalid_request"):
            raise OAuthError(err, data.get("error_description") or err)
        if err:
            # Unknown error — surface it and stop.
            raise OAuthError(err, data.get("error_description") or "unknown OAuth error")
        if "access_token" in data:
            return {
                "access_token": data["access_token"],
                "refresh_token": data.get("refresh_token", ""),
                "expires_in": int(data.get("expires_in", 7200)),
                "refresh_token_expires_in": int(data.get("refresh_token_expires_in", 2592000)),
                "scope": data.get("scope", ""),
            }
    raise OAuthError("timeout", "device flow timed out before user approved")


def refresh_user_token(
    app_id: str, app_secret: str, refresh_token: str, region: str = "feishu"
) -> dict:
    """Exchange a refresh_token for a fresh access_token (+ rotated refresh_token).

    Feishu rotates the refresh_token on each refresh — the caller MUST persist
    the new one or the next refresh will fail.
    """
    body = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": app_id,
        "client_secret": app_secret,
    }
    hosts = endpoints(region)
    r = httpx.post(
        hosts["open"] + _PATH_TOKEN_V2,
        data=body,
        timeout=20,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    data = r.json()
    if r.status_code >= 400 or "error" in data:
        raise OAuthError(
            data.get("error", f"http_{r.status_code}"),
            data.get("error_description") or "refresh_token failed — re-run auth login",
        )
    return {
        "access_token": data["access_token"],
        # Feishu sometimes returns the same refresh_token, sometimes a rotated one;
        # the caller persists whatever comes back.
        "refresh_token": data.get("refresh_token", refresh_token),
        "expires_in": int(data.get("expires_in", 7200)),
        "refresh_token_expires_in": int(data.get("refresh_token_expires_in", 2592000)),
        "scope": data.get("scope", ""),
    }
