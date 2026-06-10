"""Feishu OAuth Device Flow login.

`perform_device_login()` is the reusable core: it walks the user through the browser
consent, then writes the oauth state file the connector reads (app creds + access /
refresh tokens). It is used by the `mfs-server connector add` wizard and by
`mfs-server connector auth`, and is also runnable directly:

    python -m mfs_server.connectors.feishu.auth_login \\
        --app-id <APP_ID> --app-secret-env FEISHU_APP_SECRET \\
        --output ~/.mfs/feishu.oauth.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from .oauth import (
    DEFAULT_SCOPES,
    OAuthError,
    poll_for_device_token,
    request_device_authorization,
)


def perform_device_login(
    app_id: str,
    app_secret: str,
    output: str | Path,
    *,
    region: str = "feishu",
    scopes: Optional[list[str]] = None,
    info: Callable[[str], None] = print,
    prompt: Callable[[str], None] = print,
) -> bool:
    """Run the Feishu OAuth Device Flow and persist the token blob to `output`.

    Shows the verification URL + user code via `prompt`, polls until the user approves
    in the browser, then writes the oauth state (app creds + access/refresh tokens) the
    connector reads. Returns True on success, False on failure (reason sent via `info`).
    """
    scope_list = list(scopes) if scopes else list(DEFAULT_SCOPES)
    info(f"Requesting authorization (region={region}) …")
    try:
        dev = request_device_authorization(app_id, app_secret, scope_list, region=region)
    except OAuthError as e:
        info(f"authorization request failed: {e}")
        return False

    prompt("")
    prompt(f"  Open in your browser:  {dev['verification_uri_complete']}")
    prompt(f"  or visit {dev['verification_uri']} and enter code: {dev['user_code']}")
    prompt("")
    info("Waiting for you to approve in the browser … (Ctrl-C to abort)")
    try:
        tok = poll_for_device_token(
            app_id,
            app_secret,
            dev["device_code"],
            interval=dev["interval"],
            expires_in=dev["expires_in"],
            region=region,
        )
    except OAuthError as e:
        info(f"authorization failed: {e}")
        return False
    except KeyboardInterrupt:
        info("aborted.")
        return False

    now = int(time.time())
    blob = {
        "app_id": app_id,
        "app_secret": app_secret,
        "refresh_token": tok["refresh_token"],
        "access_token": tok["access_token"],
        "access_expires_at": now + tok.get("expires_in", 7200),
        "region": region,
        "scope": tok.get("scope", ""),
        "obtained_at": now,
    }
    out = Path(output).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(blob, ensure_ascii=False, indent=2))
    out.chmod(0o600)
    info(f"authorized — saved to {out}")
    return True


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Feishu OAuth Device Flow login.")
    p.add_argument("--app-id", required=True, help="Feishu app_id (cli_xxxxx)")
    p.add_argument(
        "--app-secret-env",
        default="FEISHU_APP_SECRET",
        help="Env var holding app_secret (default: FEISHU_APP_SECRET)",
    )
    p.add_argument("--app-secret", help="Inline app_secret (prefer --app-secret-env)")
    p.add_argument("--output", "-o", required=True, help="Path to write the oauth state file")
    p.add_argument("--scope", help="Space-separated scope list (overrides defaults)")
    p.add_argument("--region", default="feishu", choices=["feishu", "lark"], help="Cloud region")
    args = p.parse_args(argv)

    secret = args.app_secret or os.environ.get(args.app_secret_env)
    if not secret:
        print(
            f"app_secret not provided — set ${args.app_secret_env} or pass --app-secret",
            file=sys.stderr,
        )
        return 2

    def _info(msg: str) -> None:
        print(f"\033[34m[..]\033[0m {msg}")

    ok = perform_device_login(
        args.app_id,
        secret,
        args.output,
        region=args.region,
        scopes=args.scope.split() if args.scope else None,
        info=_info,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
