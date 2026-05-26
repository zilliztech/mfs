"""FastAPI /v1 control plane (design/02 §1, 03). Thin HTTP wrappers over Engine.
Typed request/response models (api/models.py) make the generated OpenAPI rich enough
for the multi-language SDKs. Phase 4: add runs synchronously (returns job_id when done);
background daemon is Phase 5+.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from ..config import ServerConfig, load_server_config
from ..engine.engine import Engine
from .models import (
    AddRequest, AddResponse, CatMeta, CatResponse, GrepResponse, JobResponse,
    LsResponse, SearchResponse, ServerInfo, StatusResponse,
)


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

    app = FastAPI(title="MFS", version="0.4.0", lifespan=lifespan,
                  description="Multi-source File-like Search — HTTP /v1 control plane.")

    def eng() -> Engine:
        return app.state.engine

    @app.get("/v1/server/info", response_model=ServerInfo, operation_id="getServerInfo", tags=["server"])
    async def server_info() -> ServerInfo:
        import socket
        return ServerInfo(version="0.4.0", machine_id=socket.gethostname(), namespace=cfg.namespace)

    @app.post("/v1/add", response_model=AddResponse, operation_id="addSource", tags=["ingest"])
    async def add(body: AddRequest) -> AddResponse:
        job_id = await eng().add(body.target, full=body.full, since=body.since)
        return AddResponse(job_id=job_id)

    @app.get("/v1/search", response_model=SearchResponse, operation_id="search", tags=["retrieval"])
    async def search(q: str, path: str | None = None, mode: str = "hybrid",
                     top_k: int = 10, collapse: bool = False) -> SearchResponse:
        connector_uri = None
        if path:
            connector_uri, _ = eng().resolve_connector_uri(path)
        results = await eng().search(q, connector_uri=connector_uri, mode=mode,
                                     top_k=top_k, collapse=collapse)
        return SearchResponse(results=results)

    @app.get("/v1/grep", response_model=GrepResponse, operation_id="grep", tags=["retrieval"])
    async def grep(pattern: str, path: str) -> GrepResponse:
        return GrepResponse(results=await eng().grep(pattern, path))

    @app.get("/v1/ls", response_model=LsResponse, operation_id="ls", tags=["browse"])
    async def ls(path: str) -> LsResponse:
        try:
            return LsResponse(entries=await eng().ls(path))
        except (FileNotFoundError, NotADirectoryError, ValueError) as e:
            raise HTTPException(404, str(e))

    @app.get("/v1/cat", operation_id="cat", tags=["browse"],
             response_model=None, responses={200: {"model": CatResponse}})
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
        if meta:
            return CatMeta(**out) if isinstance(out, dict) else out
        return CatResponse(source=path, content=out)

    @app.get("/v1/status", response_model=StatusResponse, operation_id="status", tags=["server"])
    async def status() -> StatusResponse:
        conns = await eng().meta.fetchall(
            "SELECT root_uri, type, status FROM connectors WHERE namespace_id=?", (cfg.namespace,))
        jobs = await eng().meta.fetchall(
            "SELECT status, count(*) AS n FROM connector_jobs GROUP BY status")
        return StatusResponse(connectors=[dict(c) for c in conns],
                              jobs={j["status"]: j["n"] for j in jobs})

    @app.get("/v1/jobs/{job_id}", response_model=JobResponse, operation_id="getJob", tags=["ingest"])
    async def job(job_id: str) -> JobResponse:
        row = await eng().meta.fetchone("SELECT * FROM connector_jobs WHERE id=?", (job_id,))
        if not row:
            raise HTTPException(404, "job not found")
        return JobResponse(**{k: dict(row).get(k) for k in JobResponse.model_fields})

    return app
