#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def memory_root() -> Path:
    return Path(os.getenv("OPENTAG_MEMORY_ROOT", str(Path.home() / ".mfs" / "opentag-memory")))


def memory_file(channel_id: str) -> Path:
    return memory_root() / "slack" / channel_id / "memory.md"


def ensure_memory_file(channel_id: str) -> Path:
    path = memory_file(channel_id)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(
                [
                    f"# Open Tag Memory: {channel_id}",
                    "",
                    "## Preferences",
                    "- Answers should be concise.",
                    "",
                    "## Decisions",
                    "",
                    "## Facts",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    return path


def token_from_env() -> str | None:
    if os.getenv("MFS_TOKEN"):
        return os.environ["MFS_TOKEN"]
    token_file = Path.home() / ".mfs" / "server.token"
    if token_file.exists():
        return token_file.read_text().strip()
    return None


def sync_memory_root() -> str:
    base = os.getenv("MFS_URL", "http://127.0.0.1:13619").rstrip("/")
    body = json.dumps({"target": str(memory_root())}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    token = token_from_env()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{base}/v1/add", data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as response:
        data = json.loads(response.read().decode("utf-8"))
    return str(data.get("job_id", ""))


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage optional Open Tag demo memory.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    show = sub.add_parser("show")
    show.add_argument("--channel-id", required=True)

    remember = sub.add_parser("remember")
    remember.add_argument("--channel-id", required=True)
    remember.add_argument("--text", required=True)
    remember.add_argument("--sync", action="store_true")

    path_cmd = sub.add_parser("path")
    path_cmd.add_argument("--channel-id", required=True)

    sub.add_parser("sync")

    args = parser.parse_args()
    if args.cmd == "show":
        path = ensure_memory_file(args.channel_id)
        print(path.read_text(encoding="utf-8"))
        return 0
    if args.cmd == "path":
        print(ensure_memory_file(args.channel_id))
        return 0
    if args.cmd == "remember":
        path = ensure_memory_file(args.channel_id)
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with path.open("a", encoding="utf-8") as f:
            f.write(f"\n- {timestamp}: {args.text.strip()}\n")
        print(f"remembered: {path}")
        if args.sync:
            print(f"mfs_job_id: {sync_memory_root()}")
        return 0
    if args.cmd == "sync":
        print(f"mfs_job_id: {sync_memory_root()}")
        return 0
    raise AssertionError(args.cmd)


if __name__ == "__main__":
    raise SystemExit(main())
