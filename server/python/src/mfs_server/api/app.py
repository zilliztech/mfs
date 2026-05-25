"""FastAPI /v1 control plane (design/02 §1, 03). Thin HTTP wrappers over Engine.
Phase 4: add runs synchronously (returns job_id when done); background daemon is Phase 5+.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from ..config import ServerConfig, load_server_config
from ..engine.engine import Engine


def create_app(cfg: ServerConfig | None = None) -> FastAPI:
    cfg = cfg or load_server_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        eng = Engine(cfg)
        await eng.startup()
        app.state.engine = eng
        try:
            yield
        finally:
            await eng.shutdown()

    app = FastAPI(title="MFS", version="0.4.0", lifespan=lifespan)

    def eng() -> Engine:
        return app.state.engine

    @app.get("/v1/server/info")
    async def server_info():
        import socket
        return {"version": "0.4.0", "machine_id": socket.gethostname(), "namespace": cfg.namespace}

    @app.post("/v1/add")
    async def add(body: dict):
        if "target" not in body:
            raise HTTPException(400, "target required")
        job_id = await eng().add(body["target"], full=body.get("full", False), since=body.get("since"))
        return {"job_id": job_id}

    @app.get("/v1/search")
    async def search(q: str, path: str | None = None, mode: str = "hybrid",
                     top_k: int = 10, collapse: bool = False):
        connector_uri = None
        if path:
            connector_uri, _ = eng().resolve_connector_uri(path)
        results = await eng().search(q, connector_uri=connector_uri, mode=mode,
                                     top_k=top_k, collapse=collapse)
        return {"results": results}

    @app.get("/v1/grep")
    async def grep(pattern: str, path: str):
        return {"results": await eng().grep(pattern, path)}

    @app.get("/v1/ls")
    async def ls(path: str):
        try:
            return {"entries": await eng().ls(path)}
        except (FileNotFoundError, NotADirectoryError, ValueError) as e:
            raise HTTPException(404, str(e))

    @app.get("/v1/cat")
    async def cat(path: str, range: str | None = None, meta: bool = False):
        rg = None
        if range:
            a, b = range.split(":")
            rg = (int(a), int(b))
        try:
            out = await eng().cat(path, range=rg, meta=meta)
        except IsADirectoryError:
            raise HTTPException(400, "is_directory")
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(404, str(e))
        return out if meta else {"source": path, "content": out}

    @app.get("/v1/status")
    async def status():
        conns = await eng().meta.fetchall(
            "SELECT root_uri, type, status FROM connectors WHERE namespace_id=?", (cfg.namespace,))
        jobs = await eng().meta.fetchall(
            "SELECT status, count(*) AS n FROM connector_jobs GROUP BY status")
        return {"connectors": conns, "jobs": {j["status"]: j["n"] for j in jobs}}

    @app.get("/v1/jobs/{job_id}")
    async def job(job_id: str):
        row = await eng().meta.fetchone("SELECT * FROM connector_jobs WHERE id=?", (job_id,))
        if not row:
            raise HTTPException(404, "job not found")
        return row

    return app
