"""FastAPI /v1 control plane. Thin HTTP wrappers over Engine.
Typed request/response models (api/models.py) make the generated OpenAPI rich enough
for the multi-language SDKs. `add` indexes inline by default (returns job_id when done)
or enqueues for the standalone worker when process=false.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers
from starlette.requests import ClientDisconnect

from ..config import ServerConfig, load_server_config
from ..engine.engine import Engine
from .models import (
    AddRequest,
    AddResponse,
    CancelResponse,
    CatMeta,
    CatResponse,
    EstimateResponse,
    GrepResponse,
    JobResponse,
    LsResponse,
    ManifestRequest,
    ManifestResponse,
    ProbeRequest,
    ProbeResponse,
    RemoveResponse,
    SearchResponse,
    ServerInfo,
    StatusResponse,
)

# Canonical error codes -> suggested next actions. The endpoints
# raise HTTPException with the canonical code as `detail` for these cases; the handler
# below turns that into the stable {code, detail, suggestions} envelope SDKs switch on.
_CODE_SUGGESTIONS = {
    "object_too_large_for_cat": ["head", "cat --range", "export"],
    "is_directory": ["ls", "tree"],
    "range_unsupported": ["cat --meta", "export"],
    "density_unsupported": ["head", "cat --range"],
    "tail_unsupported": ["head", "cat --range"],
    "locator_not_found": ["re-search; the record may have changed"],
    "since_unsupported": ["drop --since"],
    "sync_already_running": ["mfs job list", "mfs job cancel JOB_ID"],
    "connector_removing": ["wait for removal to finish, then retry"],
    "connector_unhealthy": ["check credentials/connectivity"],
    "embedding_auth_failed": ["fix the embedding provider API key, then `mfs add` again"],
    "embedding_quota_exceeded": [
        "top up the embedding provider quota/billing, then `mfs add` again"
    ],
    "field_missing": [
        "fix the connector `[[objects]]` text_fields — a configured field is absent from the records"
    ],
    "not_found": ["check the URI"],
    "not_available": ["the connector may require an optional dependency; install its extra"],
    "top_k_too_large": [
        "lower --top-k: it exceeds the vector store's result limit (hybrid mode over-fetches, so its effective limit is higher than top_k)"
    ],
    "embedding_dim_mismatch": [
        "the embedding dimension doesn't match this collection's vectors (the collection name encodes its dim)",
        "re-run `mfs-server setup --section embedding` to set the correct dim, or re-index into a fresh collection",
    ],
}
# HTTP status -> code when `detail` isn't already a canonical code (human strings).
_STATUS_CODE = {
    400: "bad_request",
    404: "not_found",
    409: "conflict",
    422: "validation_error",
    499: "client_closed_request",
    501: "not_available",
    502: "connector_unhealthy",
}


def _auth_failure(headers: Headers, expected_token: str) -> tuple[int, dict] | None:
    values = headers.getlist("authorization")
    if len(values) > 1:
        return (
            400,
            {
                "code": "bad_request",
                "detail": "duplicate Authorization header",
                "suggestions": ["send exactly one Authorization: Bearer <token> header"],
            },
        )
    if len(values) != 1:
        return _unauthorized()

    scheme, sep, token = values[0].partition(" ")
    if sep != " " or scheme.lower() != "bearer" or token != expected_token:
        return _unauthorized()
    return None


def _unauthorized() -> tuple[int, dict]:
    return (
        401,
        {
            "code": "unauthorized",
            "detail": "missing or invalid bearer token",
            "suggestions": ["set a profile token (Authorization: Bearer <token>)"],
        },
    )


def create_app(cfg: ServerConfig | None = None) -> FastAPI:
    cfg = cfg or load_server_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        eng = Engine(cfg)
        await eng.startup()
        app.state.engine = eng
        # AIO (sqlite/single-binary): there is no separate worker process, so an enqueued
        # (--no-process) job would sit 'queued' forever. Drain it with one in-process worker.
        # CS (postgres) deployments run a dedicated `mfs-server worker`; skip there unless
        # explicitly turned on, so API replicas don't also do indexing work.
        worker_task = None
        if cfg.server.in_process_jobrunner and eng.meta.backend == "sqlite":
            worker_task = asyncio.create_task(eng.run_worker_forever(concurrency=1))
        try:
            yield
        finally:
            if worker_task is not None:
                worker_task.cancel()
                try:
                    await worker_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            await eng.shutdown()

    app = FastAPI(
        title="MFS",
        version="0.4.0",
        lifespan=lifespan,
        description="Multi-source File-like Search — HTTP /v1 control plane.",
    )

    if cfg.auth_token:

        @app.middleware("http")
        async def _auth(request: Request, call_next):
            """Bearer-token gate: when auth_token is configured,
            every request — loopback included — must carry Authorization: Bearer <token>.
            /healthz is exempt so k8s/compose liveness probes don't need the token (it
            returns no data) — see deployments/."""
            if request.url.path == "/healthz":
                return await call_next(request)
            if failure := _auth_failure(request.headers, cfg.auth_token):
                status_code, content = failure
                return JSONResponse(status_code=status_code, content=content)
            return await call_next(request)

    @app.exception_handler(HTTPException)
    async def _http_exc(_request: Request, exc: HTTPException) -> JSONResponse:
        """Wrap HTTPException into the {code, detail, suggestions} envelope.
        When `detail` is already a canonical code, surface it as `code`; otherwise derive
        `code` from the HTTP status and keep the human string as `detail`."""
        detail = exc.detail if isinstance(exc.detail, str) else "error"
        code = detail if detail in _CODE_SUGGESTIONS else _STATUS_CODE.get(exc.status_code, "error")
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": code,
                "detail": detail,
                "suggestions": _CODE_SUGGESTIONS.get(code, []),
            },
        )

    @app.exception_handler(RequestValidationError)
    async def _val_exc(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # Build the detail from only each error's location + message — deliberately DROP
        # pydantic's `input`/`url`/`ctx` fields. `input` echoes the submitted value (which
        # for a config body can be a live secret) and `url` carries the server source path;
        # `str(exc)` would leak both. Keep `detail` a plain string so the envelope shape is
        # unchanged for SDK consumers.
        parts = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", ()) if p != "body")
            msg = err.get("msg", "invalid")
            parts.append(f"{loc}: {msg}" if loc else msg)
        return JSONResponse(
            status_code=422,
            content={
                "code": "validation_error",
                "detail": "; ".join(parts) or "validation error",
                "suggestions": ["fix request shape"],
            },
        )

    @app.exception_handler(NotImplementedError)
    async def _not_impl_exc(_request: Request, exc: NotImplementedError) -> JSONResponse:
        """A requested connector scheme has no registered plugin — usually because its
        optional extra isn't installed (registry.load_builtin skips connectors whose
        import fails). Return a clean 501 envelope instead of a 500 + traceback, with an
        actionable hint to install the connector's extra."""
        detail = str(exc) or "not implemented"
        # message shape is "no plugin for <scheme>": surface an install hint for that extra.
        # The extra name usually equals the URI scheme, but a few differ because the SDK is
        # shared/renamed: postgres's extra is `pg` (asyncpg), and gdrive/gmail share `google`
        # (google-api-python-client). Map those so the hint names a command that exists
        # (`uv sync --extra postgres` would fail — the real extra is `pg`).
        _SCHEME_TO_EXTRA = {"postgres": "pg", "gdrive": "google", "gmail": "google"}
        scheme = detail.rsplit(" ", 1)[-1] if detail.startswith("no plugin for ") else None
        extra = _SCHEME_TO_EXTRA.get(scheme, scheme) if scheme else None
        suggestions = (
            [f"install the connector extra: uv sync --extra {extra}"]
            if scheme
            else _CODE_SUGGESTIONS["not_available"]
        )
        return JSONResponse(
            status_code=501,
            content={"code": "not_available", "detail": detail, "suggestions": suggestions},
        )

    @app.exception_handler(Exception)
    async def _unhandled_exc(_request: Request, exc: Exception) -> JSONResponse:
        """Any uncaught error still returns the stable envelope so SDKs can switch on
        `code` instead of parsing a raw 500 body."""
        return JSONResponse(
            status_code=500,
            content={"code": "internal_error", "detail": str(exc), "suggestions": []},
        )

    def eng() -> Engine:
        return app.state.engine

    @app.get(
        "/v1/server/info", response_model=ServerInfo, operation_id="getServerInfo", tags=["server"]
    )
    async def server_info() -> ServerInfo:
        import socket

        return ServerInfo(version="0.4.0", machine_id=socket.gethostname(), namespace=cfg.namespace)

    @app.post("/v1/add", response_model=AddResponse, operation_id="addSource", tags=["ingest"])
    async def add(body: AddRequest) -> AddResponse:
        try:
            job_id = await eng().add(
                body.target,
                config=body.config,
                full=body.full,
                since=body.since,
                process=body.process,
                update_config=body.update,
            )
        except ValueError as e:
            code = str(e)
            status = 409 if code in ("sync_already_running", "connector_removing") else 400
            raise HTTPException(status, code)  # -> error envelope
        return AddResponse(job_id=job_id)

    @app.post(
        "/v1/jobs/{job_id}/cancel",
        response_model=CancelResponse,
        operation_id="cancelJob",
        tags=["ingest"],
    )
    async def cancel_job(job_id: str) -> CancelResponse:
        ok = await eng().cancel_job(job_id)
        return CancelResponse(job_id=job_id, cancelled=ok)

    @app.post(
        "/v1/connectors/probe",
        response_model=ProbeResponse,
        operation_id="probeConnector",
        tags=["connectors"],
    )
    async def probe(body: ProbeRequest) -> ProbeResponse:
        return ProbeResponse(**await eng().probe(body.target, body.config))

    @app.post(
        "/v1/connectors/estimate",
        response_model=EstimateResponse,
        operation_id="estimateConnector",
        tags=["connectors"],
    )
    async def estimate(body: ProbeRequest) -> EstimateResponse:
        """Zero-billing pre-flight estimate: object/chunk/token counts via
        metadata + a local chunker/tokenizer dry-run. No embedding API calls."""
        try:
            return EstimateResponse(**await eng().estimate(body.target, body.config))
        except ValueError as e:
            # e.g. an unreachable / missing source root surfaces as connector_unhealthy;
            # return the clean envelope instead of a raw 500.
            raise HTTPException(400, str(e))

    @app.get("/v1/connectors/inspect", operation_id="inspectConnector", tags=["connectors"])
    async def inspect(target: str):
        out = await eng().inspect(target)
        if out is None:
            raise HTTPException(404, "connector not found")
        return out

    @app.delete(
        "/v1/connectors",
        response_model=RemoveResponse,
        operation_id="removeConnector",
        tags=["connectors"],
    )
    async def remove(target: str) -> RemoveResponse:
        return RemoveResponse(target=target, removed=await eng().remove_connector(target))

    @app.post(
        "/v1/upload", response_model=AddResponse, operation_id="uploadSource", tags=["ingest"]
    )
    async def upload(request: Request, name: str, process: bool = True) -> AddResponse:
        """CS upload flow: POST a tar(.gz) of a tree as the raw body (?name=<label>);
        the server stages + indexes it. For client/server without a shared filesystem."""
        try:
            data = await request.body()
        except ClientDisconnect:
            raise HTTPException(499, "client disconnected during upload")
        if not data:
            raise HTTPException(400, "empty upload body")
        try:
            out = await eng().ingest_upload(name, data, process=process)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return AddResponse(job_id=out["job_id"])

    @app.post(
        "/v1/files/manifest",
        response_model=ManifestResponse,
        operation_id="filesManifest",
        tags=["ingest"],
    )
    async def files_manifest(body: ManifestRequest) -> ManifestResponse:
        """Manifest-diff upload step ②: stat-only manifest in, need_sha1 + deletion
        candidates out. No bytes transferred here."""
        out = await eng().files_manifest(
            body.client_id, body.root, [f.model_dump() for f in body.files]
        )
        return ManifestResponse(**out)

    @app.put(
        "/v1/files/upload", response_model=AddResponse, operation_id="filesUpload", tags=["ingest"]
    )
    async def files_upload(
        request: Request, client_id: str, root: str, process: bool = True, full: bool = False
    ) -> AddResponse:
        """Manifest-diff upload step ④: PUT a tar(.gz) carrying a `.mfs-meta.json`
        member (hashes/renames/deletions) + the changed file bytes. The server applies
        it to the staging area and triggers the file-connector sync. full=true
        (--force-index/--force-upload) forces a re-index of the whole staged tree."""
        try:
            data = await request.body()
        except ClientDisconnect:
            raise HTTPException(499, "client disconnected during upload")
        if not data:
            raise HTTPException(400, "empty upload body")
        try:
            out = await eng().files_upload(client_id, root, data, process=process, full=full)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return AddResponse(job_id=out["job_id"])

    @app.get("/v1/search", response_model=SearchResponse, operation_id="search", tags=["retrieval"])
    async def search(
        q: str,
        path: str | None = None,
        mode: str = "hybrid",
        top_k: int = 10,
        collapse: bool = False,
        kind: str | None = None,
    ) -> SearchResponse:
        connector_uri = None
        object_prefix = None
        if path:
            connector_uri, object_prefix = await eng().resolve_connector_uri(path)
        # comma-separated chunk_kinds, e.g. ?kind=body,directory_summary
        chunk_kinds = [k.strip() for k in kind.split(",") if k.strip()] if kind else None
        try:
            results = await eng().search(
                q,
                connector_uri=connector_uri,
                object_prefix=object_prefix,
                mode=mode,
                top_k=top_k,
                chunk_kinds=chunk_kinds,
                collapse=collapse,
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        return SearchResponse(results=results)

    @app.get("/v1/grep", response_model=GrepResponse, operation_id="grep", tags=["retrieval"])
    async def grep(pattern: str, path: str) -> GrepResponse:
        # A scope path that resolves under no connector raises ValueError ("path not under
        # any registered connector") from _open_path — map it to a clean 404 like ls/cat
        # instead of letting it escape as a raw 500 (search returns [] for the same case;
        # grep follows the browse family's explicit not_found here).
        try:
            return GrepResponse(results=await eng().grep(pattern, path))
        except (FileNotFoundError, NotADirectoryError, ValueError) as e:
            raise HTTPException(404, str(e))

    @app.get("/v1/ls", response_model=LsResponse, operation_id="ls", tags=["browse"])
    async def ls(path: str) -> LsResponse:
        try:
            return LsResponse(**await eng().ls(path))
        except (FileNotFoundError, NotADirectoryError, ValueError) as e:
            raise HTTPException(404, str(e))

    @app.get(
        "/v1/cat",
        operation_id="cat",
        tags=["browse"],
        response_model=None,
        responses={200: {"model": CatResponse}},
    )
    async def cat(
        path: str,
        range: str | None = None,
        meta: bool = False,
        density: str | None = None,
        locator: str | None = None,
    ):
        import json as _json

        rg = None
        if range:
            # External --range is 1-based half-open [start, end) — matches
            # locator.lines, head/tail line counts, and how humans cite ranges
            # ("lines 100 to 200"). Require an explicit colon so a bare "100"
            # doesn't silently degrade to a single line or an open end. Convert
            # to 0-based half-open here; engine.cat + plugin.read stay 0-based
            # internally.
            if ":" not in range:
                raise HTTPException(
                    400, "invalid range: expected start:end (1-based, end-exclusive)"
                )
            a, _, b = range.partition(":")
            try:
                start_1 = int(a) if a.strip() else 1
                end_1 = int(b) if b.strip() else (2**63 - 1)
            except ValueError:
                raise HTTPException(400, "invalid range")
            if start_1 < 1:
                raise HTTPException(400, "invalid range: start must be >= 1")
            if end_1 < start_1:
                raise HTTPException(400, "invalid range: end must be >= start")
            rg = (start_1 - 1, end_1 - 1)
        loc = None
        if locator:
            try:
                loc = _json.loads(locator)
            except ValueError:
                raise HTTPException(400, "invalid locator JSON")
        try:
            out = await eng().cat(path, range=rg, meta=meta, density=density, locator=loc)
        except IsADirectoryError:
            raise HTTPException(400, "is_directory")
        except ValueError as e:
            code = str(e)
            if code in ("density_unsupported", "range_unsupported", "object_too_large_for_cat"):
                raise HTTPException(400, code)
            if code == "locator_not_found":
                raise HTTPException(404, "locator_not_found")
            raise HTTPException(404, code)
        except FileNotFoundError as e:
            raise HTTPException(404, str(e))
        if meta:
            return CatMeta(**out) if isinstance(out, dict) else out
        if isinstance(out, dict):  # locator hit -> {source, locator, content}
            return CatResponse(source=out.get("source", path), content=out.get("content", ""))
        return CatResponse(source=path, content=out)

    async def _read_op(fn, path: str):
        """Shared error mapping for head/tail/export."""
        try:
            return await fn(path)
        except IsADirectoryError:
            raise HTTPException(400, "is_directory")
        except FileNotFoundError as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.get("/v1/head", response_model=CatResponse, operation_id="head", tags=["browse"])
    async def head(path: str, n: int = 20) -> CatResponse:
        return CatResponse(source=path, content=await _read_op(lambda p: eng().head(p, n), path))

    @app.get("/v1/tail", response_model=CatResponse, operation_id="tail", tags=["browse"])
    async def tail(path: str, n: int = 20) -> CatResponse:
        return CatResponse(source=path, content=await _read_op(lambda p: eng().tail(p, n), path))

    @app.get("/v1/export", response_model=CatResponse, operation_id="export", tags=["browse"])
    async def export(path: str) -> CatResponse:
        """Full object content for `mfs export`. Honest about completeness:
        each connector's own row cap still applies (postgres `max_read_rows`,
        BigQuery `max_read_rows`, etc.), so structured objects above that
        threshold return `partial=true`. The bare-cat size guard
        (object_too_large_for_cat) does NOT apply — export is the escape
        hatch for that — but true streaming export is still TODO."""
        text, partial = await _read_op(eng().export, path)
        return CatResponse(source=path, content=text, partial=partial)

    @app.get("/healthz", tags=["server"])
    async def healthz() -> dict:
        """Unauthenticated liveness/readiness probe (no sensitive data); used by the
        compose healthcheck and Helm probes so they work even with auth enabled."""
        return {"status": "ok"}

    @app.get("/v1/status", response_model=StatusResponse, operation_id="status", tags=["server"])
    async def status() -> StatusResponse:
        # Per-connector object/chunk counts come from the metadata `objects` table
        # (objects.chunk_count is already maintained per object). One grouped LEFT JOIN —
        # connectors with nothing indexed yet still report 0/0 — so status surfaces store
        # state without a full Milvus scan.
        conns = await eng().meta.fetchall(
            "SELECT c.root_uri AS root_uri, c.type AS type, c.status AS status, "
            "  COUNT(o.object_uri) AS object_count, "
            "  COALESCE(SUM(o.chunk_count), 0) AS chunk_count "
            "FROM connectors c LEFT JOIN objects o ON o.connector_id = c.id "
            "WHERE c.namespace_id=? GROUP BY c.id, c.root_uri, c.type, c.status",
            (cfg.namespace,),
        )
        jobs = await eng().meta.fetchall(
            "SELECT status, count(*) AS n FROM connector_jobs GROUP BY status"
        )
        return StatusResponse(
            connectors=[dict(c) for c in conns], jobs={j["status"]: j["n"] for j in jobs}
        )

    @app.get("/v1/jobs", response_model=list[JobResponse], operation_id="listJobs", tags=["ingest"])
    async def list_jobs(limit: int = 20) -> list[JobResponse]:
        rows = await eng().meta.fetchall(
            "SELECT * FROM connector_jobs ORDER BY started_at DESC LIMIT ?", (limit,)
        )
        return [JobResponse(**{k: dict(r).get(k) for k in JobResponse.model_fields}) for r in rows]

    @app.get(
        "/v1/jobs/{job_id}", response_model=JobResponse, operation_id="getJob", tags=["ingest"]
    )
    async def job(job_id: str) -> JobResponse:
        row = await eng().meta.fetchone("SELECT * FROM connector_jobs WHERE id=?", (job_id,))
        if not row:
            raise HTTPException(404, "job not found")
        return JobResponse(**{k: dict(row).get(k) for k in JobResponse.model_fields})

    return app
