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
        # Standalone worker: poll the DB queue and process queued jobs (design/02 §5).
        # Use with `mfs add --no-process` / API enqueue so ingestion runs out-of-band.
        import asyncio

        from ..config import load_server_config
        from ..engine.engine import Engine

        cfg = load_server_config(args.config)
        eng = Engine(cfg)

        async def run() -> None:
            await eng.startup()
            print(f"mfs-server worker: polling queue (metadata={cfg.metadata.backend}, "
                  f"concurrency={args.concurrency})", flush=True)
            try:
                await eng.run_worker_forever(concurrency=args.concurrency)
            finally:
                await eng.shutdown()

        try:
            asyncio.run(run())
        except KeyboardInterrupt:
            print("worker stopped")
        return 0

    if args.cmd == "reload":
        # Validate server.toml and report the resolved backends. (Hot-reload of a
        # running process needs a control socket; restart to apply — design/10 §7.)
        from ..config import load_server_config

        try:
            cfg = load_server_config(args.config)
        except Exception as e:  # noqa: BLE001
            print(f"config invalid: {e}")
            return 1
        print(f"config OK — milvus={'lite' if not cfg.milvus.uri.startswith('http') else 'remote'}, "
              f"metadata={cfg.metadata.backend}, object_store={cfg.object_store.backend}. "
              "Restart the server process to apply changes.")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
