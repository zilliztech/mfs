"""Standalone CLI: walk the user through Feishu OAuth Device Flow and persist
the resulting refresh_token blob to a file the connector reads + writes via
its `oauth_state_file` config field.

Usage:
    python -m mfs_server.connectors.feishu.auth_login \\
        --app-id <APP_ID> \\
        --app-secret-env FEISHU_APP_SECRET \\
        --output ~/.feishu/oauth.json
        [--scope "im:chat:readonly im:message.group_msg:readonly ..."]

Output JSON shape (the file the connector reads + rewrites each connect; NOT
credential_ref because Feishu refresh_tokens rotate every refresh):
    { "app_id": "...", "app_secret": "...", "refresh_token": "...",
      "scope": "...", "obtained_at": <unix-ts>, "refresh_expires_at": <unix-ts> }
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from .oauth import (
    DEFAULT_SCOPES,
    OAuthError,
    poll_for_device_token,
    request_device_authorization,
)


def _ok(msg: str) -> None:
    print(f"\033[32m[OK]\033[0m {msg}")


def _info(msg: str) -> None:
    print(f"\033[34m[..]\033[0m {msg}")


def _fail(msg: str) -> None:
    print(f"\033[31m[!!]\033[0m {msg}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--app-id", required=True, help="Feishu app_id (cli_xxxxx)")
    p.add_argument(
        "--app-secret-env",
        default="FEISHU_APP_SECRET",
        help="Env var holding app_secret (default: FEISHU_APP_SECRET)",
    )
    p.add_argument("--app-secret", help="Inline app_secret (insecure; prefer --app-secret-env)")
    p.add_argument(
        "--output",
        "-o",
        required=True,
        help="Path to write oauth.json (referenced by oauth_state_file in config)",
    )
    p.add_argument("--scope", help="Space-separated scope list (overrides defaults)")
    p.add_argument(
        "--region",
        default="feishu",
        choices=["feishu", "lark"],
        help="Cloud region (default: feishu / China; use 'lark' for overseas)",
    )
    args = p.parse_args(argv)

    secret = args.app_secret or os.environ.get(args.app_secret_env)
    if not secret:
        _fail(f"app_secret not provided — set ${args.app_secret_env} or pass --app-secret")
        return 2

    scopes = args.scope.split() if args.scope else DEFAULT_SCOPES

    _info(f"Requesting device authorization (region={args.region}, scopes: {' '.join(scopes)}) ...")
    try:
        dev = request_device_authorization(args.app_id, secret, scopes, region=args.region)
    except OAuthError as e:
        _fail(f"device_authorization failed: {e}")
        return 1
    _ok(f"got device_code (expires in {dev['expires_in']}s, poll every {dev['interval']}s)")

    # The verification UI: print BOTH the plain URL and the complete URL with user_code
    # pre-filled. User can use either.
    print()
    print(f"  Open in browser:  {dev['verification_uri_complete']}")
    print(f"  Or visit:         {dev['verification_uri']}")
    print(f"  And enter code:   {dev['user_code']}")
    print()

    _info("Polling for approval ... (Ctrl-C to abort)")
    try:
        tok = poll_for_device_token(
            args.app_id,
            secret,
            dev["device_code"],
            interval=dev["interval"],
            expires_in=dev["expires_in"],
            region=args.region,
        )
    except OAuthError as e:
        _fail(f"device flow failed: {e}")
        return 1
    except KeyboardInterrupt:
        _fail("aborted by user")
        return 130
    _ok(f"got access_token + refresh_token (scope: {tok.get('scope') or 'default'})")

    now = int(time.time())
    blob = {
        "app_id": args.app_id,
        "app_secret": secret,
        "refresh_token": tok["refresh_token"],
        "region": args.region,
        "scope": tok.get("scope", ""),
        "obtained_at": now,
        "refresh_expires_at": now + tok.get("refresh_token_expires_in", 2592000),
    }
    out = Path(args.output).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(blob, ensure_ascii=False, indent=2))
    out.chmod(0o600)
    _ok(f"saved to {out}  (chmod 600)")

    print()
    print("Next steps:")
    print(f"  1) In your feishu connector TOML, set:")
    print(f'       auth = "user"')
    print(f'       oauth_state_file = "{out}"')
    print(f"     (NOT credential_ref — Feishu refresh_tokens rotate on every use, so the")
    print(f"      plugin needs to WRITE this file each connect to persist the new token.")
    print(f"      app_id / app_secret are inside the file too; no other config needed.)")
    refresh_days = blob["refresh_expires_at"] - now
    print(
        f"  2) Refresh token expires in ~{refresh_days // 86400} days "
        f"(Feishu's TTL, not configurable); re-run this script before then."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
