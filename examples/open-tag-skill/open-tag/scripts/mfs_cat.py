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


def is_path_allowed(path: str, allowed_scopes: list[str]) -> bool:
    if "--all" in allowed_scopes:
        return True
    for allowed in allowed_scopes:
        normalized = allowed.rstrip("/")
        if path == normalized or path.startswith(f"{normalized}/"):
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Read an MFS object or locator for Open Tag.")
    parser.add_argument("path")
    parser.add_argument("--locator", help="JSON locator from an MFS search hit.")
    args = parser.parse_args()

    allowed_scopes = parse_scopes(os.getenv("MFS_ALLOWED_SCOPES", ""))
    if not allowed_scopes:
        print("MFS_ALLOWED_SCOPES is required.", file=sys.stderr)
        return 2
    if not is_path_allowed(args.path, allowed_scopes):
        print(f"Path is outside MFS_ALLOWED_SCOPES: {args.path}", file=sys.stderr)
        return 2

    params: dict[str, Any] = {"path": args.path}
    if args.locator:
        json.loads(args.locator)
        params["locator"] = args.locator
    data = request_json("/v1/cat", params)
    print(data.get("content", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
