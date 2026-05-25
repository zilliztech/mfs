"""mfs-server entrypoint (design/02 §5): run | api | worker | reload.

Phase 4: run/api start the FastAPI app (add is processed synchronously within the
request). Standalone worker daemon (polling the DB queue) + reload are Phase 5/7.
"""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mfs-server")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("run", "api"):
        sp = sub.add_parser(name)
        sp.add_argument("--bind", default="127.0.0.1:8765")
        sp.add_argument("--config", default=None)
    wk = sub.add_parser("worker")
    wk.add_argument("--config", default=None)
    wk.add_argument("--concurrency", default="auto")
    rl = sub.add_parser("reload")
    rl.add_argument("--config", default=None)

    args = p.parse_args(argv)

    if args.cmd in ("run", "api"):
        import uvicorn

        from ..api.app import create_app
        from ..config import load_server_config

        cfg = load_server_config(args.config)
        host, _, port = args.bind.partition(":")
        app = create_app(cfg)
        uvicorn.run(app, host=host, port=int(port or "8765"))
        return 0

    if args.cmd == "worker":
        # Phase 5: standalone worker polling the DB queue. v0.4 Phase 4 processes tasks
        # inline within `add`, so a separate worker is not yet required.
        print("standalone worker not yet implemented (tasks processed inline in `add` for now)")
        return 0

    if args.cmd == "reload":
        print("reload not yet implemented; restart the process to apply server.toml changes")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
