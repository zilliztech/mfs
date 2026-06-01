"""Pydantic request/response models for the /v1 control plane.

These give the generated OpenAPI typed schemas so the
multi-language SDKs (python/typescript/go/java) get real models instead of opaque
dicts.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ServerInfo(BaseModel):
    version: str = Field(..., description="server semver, e.g. 0.4.0")
    machine_id: str = Field(..., description="host identifier")
    namespace: str = Field(..., description="active namespace")


class AddRequest(BaseModel):
    target: str = Field(..., description="path or connector URI to register + index")
    config: Optional[dict[str, Any]] = Field(
        None,
        description="connector config ([[objects]], schemas, _credential_ref, ...); "
        "the CLI loads this from --config <file.toml>",
    )
    full: bool = Field(False, description="force full re-index (ignore caches/fingerprints)")
    since: Optional[str] = Field(None, description="only index changes since this cursor/date")
    process: bool = Field(
        False,
        description="False (default): enqueue and return the job_id immediately "
        "(poll GET /v1/jobs/{id}); True: index inline and return when done",
    )
    update: bool = Field(
        False,
        description="apply config to an existing connector (connector update); add ignores it",
    )


class CancelResponse(BaseModel):
    job_id: str
    cancelled: bool


class ProbeRequest(BaseModel):
    target: str
    config: Optional[dict[str, Any]] = None


class ProbeResponse(BaseModel):
    target: str
    type: str
    ok: bool
    detail: str = ""


class RemoveResponse(BaseModel):
    target: str
    removed: bool


class EstimateResponse(BaseModel):
    target: str
    type: str
    objects: int = Field(..., description="total objects discovered (metadata-only count)")
    sampled_objects: int = Field(..., description="objects actually sampled for the dry-run")
    est_chunks: int = Field(..., description="extrapolated chunk count (±50%)")
    est_tokens: int = Field(
        ..., description="extrapolated token count; apply your provider rate for $"
    )


class AddResponse(BaseModel):
    job_id: str = Field(..., description="sync job id; poll GET /v1/jobs/{job_id}")


class ManifestFile(BaseModel):
    path: str = Field(..., description="path relative to the upload root")
    size: int
    mtime_ns: int
    inode: Optional[int] = None


class ManifestRequest(BaseModel):
    client_id: str = Field(
        ..., description="stable client id (from client.toml) — connector identity"
    )
    root: str = Field(..., description="client absolute path of the upload root (identity + scope)")
    files: list[ManifestFile] = Field(default_factory=list, description="stat-only manifest")


class DeletionCandidate(BaseModel):
    path: str
    size: Optional[int] = None
    inode: Optional[int] = None
    sha1: Optional[str] = None


class ManifestResponse(BaseModel):
    connector_uri: str
    staging: str
    need_sha1: list[str] = Field(
        default_factory=list, description="paths whose bytes the server needs"
    )
    deletion_candidates: list[DeletionCandidate] = Field(
        default_factory=list,
        description="server-known paths absent from the manifest (for rename pairing)",
    )


class ResultEnvelope(BaseModel):
    """One search/grep hit. Outer shape is stable across connectors;
    locator + metadata.fields are per-connector but documented."""

    source: str = Field(..., description="object URI — feed to cat/head/export")
    content: str = Field("", description="snippet to read")
    score: Optional[float] = Field(None, description="ranking score; <0.5 often unreliable")
    locator: Optional[dict[str, Any]] = Field(
        None,
        description=(
            "per-chunk identity. body/code/document chunks: "
            "{'lines':[start,end]}; structured (DB row, issue, slack "
            "thread): connector PK dict (e.g. {'id':1}, {'number':42}, "
            "{'thread_ts':'...'}); once-per-object kinds: null."
        ),
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="chunk_kind, connector_type, fields, ..."
    )


class SearchResponse(BaseModel):
    results: list[ResultEnvelope]


class GrepMatchModel(BaseModel):
    source: Optional[str] = None
    locator: Optional[dict[str, Any]] = Field(
        None,
        description=(
            "per-hit identity. text/code line hits: {'lines':[n,n]}; "
            "structured pushdown: connector PK dict; notice rows: null."
        ),
    )
    content: str = ""
    via: Optional[str] = Field(None, description="bm25 | linear | pushdown | notice")


class GrepResponse(BaseModel):
    results: list[GrepMatchModel]


class LsEntry(BaseModel):
    name: str
    type: str = Field(..., description="file | dir")
    media_type: Optional[str] = None
    size_hint: Optional[int] = None
    path: Optional[str] = Field(None, description="full object URI — feed to cat/search")
    search_status: Optional[str] = Field(
        None, description="indexed | partial | not_indexed | null if unseen"
    )
    indexable: Optional[bool] = Field(
        None, description="whether this object is eligible for indexing"
    )


class LsResponse(BaseModel):
    entries: list[LsEntry]
    capabilities: dict[str, Any] = Field(
        default_factory=dict, description="connector capabilities (sync/object)"
    )


class CatResponse(BaseModel):
    source: str
    content: str


class CatMeta(BaseModel):
    source: str
    media_type: Optional[str] = None
    size_hint: Optional[int] = None
    fingerprint: Optional[str] = None


class ConnectorRow(BaseModel):
    root_uri: str
    type: str
    status: str


class StatusResponse(BaseModel):
    connectors: list[ConnectorRow]
    jobs: dict[str, int] = Field(default_factory=dict, description="count of jobs by status")


class JobResponse(BaseModel):
    id: str
    status: str
    op_kind: Optional[str] = None
    trigger: Optional[str] = None
    error: Optional[str] = None
    total_objects: Optional[int] = None
    succeeded_objects: Optional[int] = None
    failed_objects: Optional[int] = None
    cancelled_objects: Optional[int] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class ErrorResponse(BaseModel):
    code: str = Field(..., description="stable error code")
    detail: str = ""
    suggestions: list[str] = Field(default_factory=list)
