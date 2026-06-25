#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def token_from_env() -> str | None:
    if os.getenv("MFS_TOKEN"):
        return os.environ["MFS_TOKEN"]
    token_file = Path.home() / ".mfs" / "server.token"
    if token_file.exists():
        return token_file.read_text().strip()
    return None


def request_json(path: str, params: dict[str, Any]) -> dict[str, Any]:
    base = os.getenv("MFS_URL", "http://127.0.0.1:13619").rstrip("/")
    url = f"{base}{path}?{urllib.parse.urlencode(params)}"
    headers = {}
    token = token_from_env()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_scopes(raw: str) -> list[str]:
    return [scope.strip() for scope in raw.split(",") if scope.strip()]


def is_scope_allowed(target: str, allowed_scopes: list[str]) -> bool:
    if "--all" in allowed_scopes:
        return True
    for allowed in allowed_scopes:
        normalized = allowed.rstrip("/")
        if target == normalized or target.startswith(f"{normalized}/"):
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Search MFS scopes for Open Tag.")
    parser.add_argument("query")
    parser.add_argument("--scope", action="append", default=[])
    parser.add_argument("--mode", choices=["hybrid", "semantic", "keyword"], default="hybrid")
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--collapse", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--json", action="store_true", help="Print raw JSON response.")
    args = parser.parse_args()

    allowed_scopes = parse_scopes(os.getenv("MFS_ALLOWED_SCOPES", ""))
    if not allowed_scopes:
        print("MFS_ALLOWED_SCOPES is required.", file=sys.stderr)
        return 2

    scopes = args.scope or allowed_scopes
    if not scopes:
        print(
            "No scopes supplied. Set MFS_ALLOWED_SCOPES or pass --scope.",
            file=sys.stderr,
        )
        return 2

    all_results: list[dict[str, Any]] = []
    for scope in scopes:
        if not is_scope_allowed(scope, allowed_scopes):
            print(f"Scope is outside MFS_ALLOWED_SCOPES: {scope}", file=sys.stderr)
            return 2
        params: dict[str, Any] = {
            "q": args.query,
            "mode": args.mode,
            "top_k": args.top_k,
            "collapse": "true" if args.collapse else "false",
        }
        if scope != "--all":
            params["path"] = scope
        data = request_json("/v1/search", params)
        all_results.extend(data.get("results") or [])

    all_results.sort(key=lambda item: item.get("score") or 0, reverse=True)
    if args.json:
        print(json.dumps({"results": all_results}, ensure_ascii=False, indent=2))
        return 0

    for idx, hit in enumerate(all_results[: args.top_k], start=1):
        source = hit.get("source", "unknown-source")
        locator = hit.get("locator")
        content = " ".join((hit.get("content") or "").split())
        if len(content) > 360:
            content = content[:357].rstrip() + "..."
        print(f"[{idx}] {source}")
        if locator:
            print(f"    locator: {json.dumps(locator, ensure_ascii=False)}")
        if content:
            print(f"    {content}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
