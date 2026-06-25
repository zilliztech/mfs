#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def env(name: str) -> str:
    return os.getenv(name, "").strip()


def token_from_env() -> str | None:
    if env("MFS_TOKEN"):
        return env("MFS_TOKEN")
    token_file = Path.home() / ".mfs" / "server.token"
    if token_file.exists():
        return token_file.read_text().strip()
    return None


def print_check(ok: bool, label: str, detail: str = "") -> None:
    status = "ok" if ok else "fail"
    suffix = f" - {detail}" if detail else ""
    print(f"[{status}] {label}{suffix}")


def request_json(
    url: str, *, token: str | None = None, timeout: int = 20
) -> tuple[bool, dict[str, Any]]:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return True, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            body = {"error": str(exc)}
        return False, body
    except Exception as exc:  # noqa: BLE001
        return False, {"error": f"{type(exc).__name__}: {exc}"}


def slack_api(
    method: str, token: str, params: dict[str, str] | None = None
) -> tuple[bool, dict[str, Any]]:
    query = f"?{urllib.parse.urlencode(params or {})}" if params else ""
    ok, data = request_json(f"https://slack.com/api/{method}{query}", token=token)
    return ok and bool(data.get("ok")), data


def check_env() -> bool:
    required = [
        "SLACK_APP_TOKEN",
        "SLACK_BOT_TOKEN",
        "MFS_URL",
        "MFS_ALLOWED_SCOPES",
        "OPENTAG_BACKEND",
    ]
    all_ok = True
    for name in required:
        value = env(name)
        ok = bool(value)
        all_ok = all_ok and ok
        detail = "set" if ok else "missing"
        if name == "SLACK_APP_TOKEN" and value:
            detail = (
                "set, expected xapp-* token"
                if value.startswith("xapp-")
                else "set, unexpected prefix"
            )
            ok = value.startswith("xapp-")
        if name == "SLACK_BOT_TOKEN" and value:
            detail = (
                "set, expected xoxb-* token"
                if value.startswith("xoxb-")
                else "set, unexpected prefix"
            )
            ok = value.startswith("xoxb-")
        print_check(ok, name, detail)
        all_ok = all_ok and ok

    mfs_token = token_from_env()
    print_check(
        bool(mfs_token),
        "MFS_TOKEN or ~/.mfs/server.token",
        "available" if mfs_token else "missing",
    )
    return all_ok and bool(mfs_token)


def check_mfs(scopes: list[str]) -> bool:
    base = env("MFS_URL").rstrip("/") or "http://127.0.0.1:13619"
    token = token_from_env()
    ok, data = request_json(f"{base}/healthz")
    print_check(ok, "MFS healthz", data.get("status") or data.get("error", "reachable"))
    if not ok:
        print(f"       hint: MFS server is not reachable at {base}.")
        print("       Install and start it first: uv tool install mfs-server && mfs-server run")
    all_ok = ok

    ok, data = request_json(f"{base}/v1/status", token=token)
    connector_count = len(data.get("connectors") or []) if isinstance(data, dict) else 0
    print_check(
        ok,
        "MFS /v1/status",
        f"{connector_count} connectors" if ok else str(data.get("error")),
    )
    if ok and connector_count == 0:
        print("       hint: no sources indexed yet. Add one with the mfs-ingest skill.")
    all_ok = all_ok and ok

    for scope in scopes:
        params = urllib.parse.urlencode({"path": scope})
        ok, data = request_json(f"{base}/v1/ls?{params}", token=token)
        detail = "listed" if ok else str(data.get("error") or data.get("detail"))
        print_check(ok, f"MFS scope {scope}", detail)
        all_ok = all_ok and ok
    return all_ok


def check_slack(channel_id: str | None) -> bool:
    bot_token = env("SLACK_BOT_TOKEN")
    all_ok = True
    ok, data = slack_api("auth.test", bot_token)
    detail = data.get("team") or data.get("error") or "authenticated"
    print_check(ok, "Slack bot auth.test", detail)
    all_ok = all_ok and ok

    if channel_id:
        ok, data = slack_api("conversations.info", bot_token, {"channel": channel_id})
        if ok:
            channel = data.get("channel") or {}
            detail = f"name={channel.get('name')}, member={channel.get('is_member')}, private={channel.get('is_private')}"
        else:
            detail = data.get("error") or "failed"
        print_check(ok, f"Slack channel {channel_id}", detail)
        all_ok = all_ok and ok

        ok, data = slack_api(
            "conversations.history", bot_token, {"channel": channel_id, "limit": "1"}
        )
        detail = "history readable" if ok else data.get("error") or "failed"
        print_check(ok, f"Slack channel history {channel_id}", detail)
        all_ok = all_ok and ok

    return all_ok


def check_backend() -> bool:
    backend = env("OPENTAG_BACKEND")
    if backend == "claude":
        ok = shutil.which("claude") is not None
        print_check(
            ok,
            "backend claude",
            "claude executable found" if ok else "claude executable missing",
        )
        return ok
    if backend == "codex":
        ok = shutil.which("codex") is not None
        print_check(
            ok,
            "backend codex",
            "codex executable found" if ok else "codex executable missing",
        )
        return ok
    print_check(False, "OPENTAG_BACKEND", "must be claude or codex")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Preflight an Open Tag Slack + MFS setup.")
    parser.add_argument("--channel-id", help="Optional Slack channel ID to verify bot access.")
    args = parser.parse_args()

    scopes = [scope.strip() for scope in env("MFS_ALLOWED_SCOPES").split(",") if scope.strip()]
    checks = [
        check_env(),
        check_slack(args.channel_id),
        check_mfs(scopes) if scopes else False,
        check_backend(),
    ]
    return 0 if all(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
