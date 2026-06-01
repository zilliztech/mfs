"""mfs-server entrypoint: run | api | worker | reload.

Phase 4: run/api start the FastAPI app (add is processed synchronously within the
request). Standalone worker daemon (polling the DB queue) + reload are Phase 5/7.
"""

from __future__ import annotations

import argparse
import sys


def _ensure_auth_token(cfg) -> None:
    """Bootstrap a Bearer token so the HTTP API is never exposed unauthenticated by
    default, loopback included. If none is configured, reuse or mint
    ~/.mfs/server.token; the CLI reads the same file on this host. Set auth_token in
    server.toml (or MFS_API_TOKEN) to override, or "-" to explicitly run open."""
    import secrets
    from pathlib import Path

    if cfg.auth_token:
        if cfg.auth_token == "-":  # explicit opt-out for trusted/isolated networks
            cfg.auth_token = ""
        return
    token_file = Path(cfg.home or ".") / "server.token"
    if token_file.exists():
        cfg.auth_token = token_file.read_text().strip()
        return
    tok = secrets.token_urlsafe(32)
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(tok)
    try:
        token_file.chmod(0o600)
    except OSError:
        pass
    cfg.auth_token = tok
    print(
        f"mfs-server: generated API token at {token_file} "
        f"(local CLIs read it automatically; pass it as Authorization: Bearer for remote)",
        flush=True,
    )


def main(argv: list[str] | None = None) -> int:
    # `setup` / `connector` are special — each has its own argparse, so we
    # route on the first positional and forward the rest verbatim.
    raw = list(argv if argv is not None else sys.argv[1:])
    if raw and raw[0] == "setup":
        from .setup_wizard import main_entry

        return main_entry(raw[1:])
    if raw and raw[0] == "connector":
        # `mfs-server connector add <uri> ...` — only `add` is implemented for
        # now; other verbs (list/inspect/remove) live on the regular `mfs`
        # CLI today.
        from .connector_wizard import main_entry as connector_main

        if len(raw) >= 2 and raw[1] == "add":
            return connector_main(raw[2:])
        print(
            "usage: mfs-server connector add <uri> [options]\n"
            "  see `mfs-server connector add --help` for the wizard",
            file=sys.stderr,
        )
        return 2

    p = argparse.ArgumentParser(prog="mfs-server")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("run", "api"):
        sp = sub.add_parser(name)
        sp.add_argument("--bind", default="127.0.0.1:13619")
        sp.add_argument("--config", default=None)
    wk = sub.add_parser("worker")
    wk.add_argument("--config", default=None)
    wk.add_argument("--concurrency", default="auto")
    rl = sub.add_parser("reload")
    rl.add_argument("--config", default=None)
    # Stubs so `mfs-server <subcmd> --help` shows up in the top-level help
    # even though dispatch happens before argparse sees it.
    sub.add_parser("setup", help="Interactive base config wizard (writes server.toml).")
    sub.add_parser(
        "connector",
        help="Connector subcommands. Currently: `connector add <uri>` (interactive wizard).",
    )

    args = p.parse_args(raw)

    if args.cmd in ("run", "api"):
        import uvicorn

        from ..api.app import create_app
        from ..config import load_server_config

        cfg = load_server_config(args.config)
        _ensure_auth_token(cfg)
        host, _, port = args.bind.partition(":")
        app = create_app(cfg)
        uvicorn.run(app, host=host, port=int(port or "13619"))
        return 0

    if args.cmd == "worker":
        # Standalone worker: poll the DB queue and process queued jobs.
        # Use with `mfs add --no-process` / API enqueue so ingestion runs out-of-band.
        import asyncio

        from ..config import load_server_config
        from ..engine.engine import Engine

        cfg = load_server_config(args.config)
        eng = Engine(cfg)

        async def run() -> None:
            await eng.startup()
            print(
                f"mfs-server worker: polling queue (metadata={cfg.metadata.backend}, "
                f"concurrency={args.concurrency})",
                flush=True,
            )
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
        # running process needs a control socket; restart to apply.)
        from ..config import load_server_config

        try:
            cfg = load_server_config(args.config)
        except Exception as e:  # noqa: BLE001
            print(f"config invalid: {e}")
            return 1
        print(
            f"config OK — milvus={'lite' if not cfg.milvus.uri.startswith('http') else 'remote'}, "
            f"metadata={cfg.metadata.backend}, object_store={cfg.object_store.backend}. "
            "Restart the server process to apply changes."
        )
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
