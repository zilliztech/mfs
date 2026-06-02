"""Server configuration: load from server.toml (lookup chain) + env overrides.

Lookup order: --config arg -> $MFS_SERVER_CONFIG -> ./server.toml
-> $MFS_HOME/server.toml (if MFS_HOME is set) -> ~/.mfs/server.toml
-> /etc/mfs/server.toml -> built-in defaults.

Outward concept map (design doc §2 terms table):
  Database  — one relational backend (sqlite | postgres) used for metadata
              (connectors, objects, queue) AND the transformation cache
              lookup table. Power users wanting split backends can still
              override [metadata] / [transformation_cache] explicitly.
  Cache     — one outward "Cache" concept covering both halves:
              - artifact half: blobs (PDF→md, VLM summaries) under
                [artifact_cache] (backend = local | s3)
              - transformation half: KV lookups under
                [transformation_cache] (policy; backend inherits from
                [database])

The wizard writes [database] + [artifact_cache] directly. Legacy tomls
with [metadata] backend / [object_store] / [artifact_cache] policy are
auto-migrated at load time (see _migrate_legacy_blocks).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel

if sys.version_info < (3, 11):
    import tomli as tomllib
else:
    import tomllib


def mfs_home() -> Path:
    home = Path(os.environ.get("MFS_HOME", str(Path.home() / ".mfs")))
    home.mkdir(parents=True, exist_ok=True)
    return home


class DatabaseConfig(BaseModel):
    """Single backend for all relational state.

    Covers metadata (connector registry, objects, queue, watch grants) and
    the transformation-cache lookup table (sha1(input) → embedding/summary
    bytes). Splitting these between two backends is a power-user choice; the
    wizard always writes a single [database] block.
    """

    backend: str = "sqlite"  # sqlite | postgres
    dsn: str = ""  # postgres DSN; ignored for sqlite


class MetadataConfig(BaseModel):
    """Per-store knobs for the metadata half.

    backend/dsn here override [database] for power users who want
    metadata on a different backend than the transformation cache.
    Blank means "inherit from [database]".
    """

    path: str = ""  # sqlite file (default $MFS_HOME/metadata.db)
    backend: str = ""  # blank = inherit from [database]
    dsn: str = ""  # blank = inherit from [database]


class TransformationCacheConfig(BaseModel):
    """Transformation half of the outward Cache concept.

    Stores per-input KV: sha1(input) → bytes (embeddings, small summaries).
    Backend/dsn default to [database]; the policy knobs here (size, flush,
    batch) only matter for the cache layer's runtime behaviour.
    """

    enabled: bool = True
    db_path: str = ""  # default $MFS_HOME/transformation_cache.db
    backend: str = ""  # blank = inherit from [database]
    dsn: str = ""  # blank = inherit from [database]
    max_size_gb: float = 5.0
    eviction_interval_s: int = 600
    write_flush_interval_s: float = 2.0
    write_buffer_max: int = 5000
    lookup_batch_size: int = 1000


class ArtifactCacheConfig(BaseModel):
    """Artifact half of the outward Cache concept.

    Stores derived blobs per object: PDF→markdown conversions, VLM image
    descriptions, etc. Lives on local fs (default) or in S3-compatible
    object storage (covers AWS S3 / R2 / GCS / MinIO via endpoint_url).
    Size/eviction policy lives in the same section because it acts on the
    same backend — there's no point splitting storage vs policy across
    two TOML blocks for one concept.
    """

    backend: str = "local"  # local | s3
    root: str = ""  # local root (default $MFS_HOME/cache)
    # s3 backend (also R2/GCS/MinIO via endpoint_url)
    bucket: str = ""
    prefix: str = "mfs"
    endpoint_url: str = ""  # set for R2/GCS/MinIO; empty = AWS
    region: str = "us-east-1"
    access_key_id: str = ""
    secret_access_key: str = ""
    # Eviction policy (applies to the artifact blobs regardless of backend).
    max_size_gb: float = 10.0
    eviction: str = "lru"


class MilvusConfig(BaseModel):
    uri: str = ""  # ~/.mfs/milvus.db (Lite) | https://*.zillizcloud.com
    token: str = ""
    # Empty = mfs default (Strong; see MilvusStore._cl_kw for rationale).
    # Power users can set "Bounded" (~5s staleness, Milvus SDK default) on
    # large clusters with strict P99 SLAs, or "Eventually" / "Session" to
    # tune staleness vs. latency further.
    consistency_level: str = ""
    # Optional BM25 analyzer config — passes through to Milvus
    # `enable_analyzer` on the `content` field. Empty = Milvus default
    # (standard tokenizer, English-leaning whitespace + lowercase). For
    # Chinese-heavy corpora set e.g. {"type": "chinese"}. Docs:
    # https://milvus.io/docs/analyzer-overview.md
    analyzer_params: dict = {}
    collection_strategy: str = "shared"  # shared | per_namespace
    num_partitions: int = 64


class EmbeddingConfig(BaseModel):
    # Default = local ONNX (no API key required). Model downloads from the
    # Hugging Face Hub on first use and is cached under $MFS_HOME/onnx-cache/.
    # bge-m3 is multilingual (100+ langs) and int8-quantized for CPU. Switch
    # to "openai" for the hosted embedding API; the setup wizard walks the
    # user through both paths.
    provider: str = "onnx"
    model: str = "gpahal/bge-m3-onnx-int8"
    dim: int = 1024
    batch_size: int = 100
    batch_max_wait_ms: int = 100


class SummaryConfig(BaseModel):
    enabled: bool = False  # master switch for directory summaries; off by default (opt-in)
    provider: str = "openai"
    model: str = "gpt-4o-mini"
    max_tokens: int = 800
    dir_recursive: bool = True  # bottom-up recursive directory summary (child summaries roll up)
    max_input_kb: int = 64  # total input budget fed to one directory summary (truncated)
    per_file_max_kb: int = 16  # per-file truncation cap so one big file can't eat the budget
    include_image_desc: bool = False  # feed image VLM descriptions into the directory summary
    batch_size: int = 20


class VlmConfig(BaseModel):
    provider: str = "openai"
    model: str = "gpt-4o-mini"
    prompt: str = "Describe this image in detail for search indexing."
    batch_size: int = 10


class ConverterConfig(BaseModel):
    default: str = "markitdown"


class WorkerConfig(BaseModel):
    concurrency: str | int = "auto"  # auto | <int>; sqlite forced to 1
    max_retries: int = 3
    backoff_initial_ms: int = 1000
    backoff_max_ms: int = 30000
    consecutive_fatal_threshold: int = 5
    # AIO single-binary: `mfs-server run` drains the queue in-process so an enqueued
    # (--no-process) job isn't stranded with no worker. CS deployments run a dedicated
    # `mfs-server worker` and should set this false on the API replicas.
    in_process: bool = True


class ChunkConfig(BaseModel):
    default_chunk_max: int = 1_000_000
    chunk_size: int = 2048  # chonkie token budget


class SearchConfig(BaseModel):
    over_fetch_ratio: int = 3
    max_partitions_per_query: int = 32


class ServerConfig(BaseModel):
    home: str = ""
    namespace: str = "default"
    auth_token: str = ""  # when set, /v1 requires Authorization: Bearer <token>
    database: DatabaseConfig = DatabaseConfig()
    metadata: MetadataConfig = MetadataConfig()
    transformation_cache: TransformationCacheConfig = TransformationCacheConfig()
    artifact_cache: ArtifactCacheConfig = ArtifactCacheConfig()
    milvus: MilvusConfig = MilvusConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
    summary: SummaryConfig = SummaryConfig()
    vlm: VlmConfig = VlmConfig()
    converter: ConverterConfig = ConverterConfig()
    worker: WorkerConfig = WorkerConfig()
    chunk: ChunkConfig = ChunkConfig()
    search: SearchConfig = SearchConfig()

    def resolve_defaults(self) -> "ServerConfig":
        home = Path(self.home) if self.home else mfs_home()
        home.mkdir(parents=True, exist_ok=True)
        self.home = str(home)
        # Propagate the unified [database] backend/dsn to the per-store
        # configs unless they explicitly override (non-blank).
        if not self.metadata.backend:
            self.metadata.backend = self.database.backend
        if not self.metadata.dsn:
            self.metadata.dsn = self.database.dsn
        if not self.transformation_cache.backend:
            self.transformation_cache.backend = self.database.backend
        if not self.transformation_cache.dsn:
            self.transformation_cache.dsn = self.database.dsn
        # On-disk default paths.
        if not self.metadata.path:
            self.metadata.path = str(home / "metadata.db")
        if not self.artifact_cache.root:
            self.artifact_cache.root = str(home / "cache")
        if not self.milvus.uri:
            self.milvus.uri = str(home / "milvus.db")  # Lite default
        if not self.transformation_cache.db_path:
            self.transformation_cache.db_path = str(home / "transformation_cache.db")
        return self


def _find_config_path(explicit: str | None) -> Path | None:
    # $MFS_HOME / server.toml is the wizard's default write target (see
    # mfs_home() above and setup_wizard.run_wizard). It must be in the
    # lookup chain or `mfs-server setup` followed by `mfs-server run` with
    # a non-default MFS_HOME silently falls back to built-in defaults.
    mfs_home_env = os.environ.get("MFS_HOME")
    candidates = [
        explicit,
        os.environ.get("MFS_SERVER_CONFIG"),
        "./server.toml",
        f"{mfs_home_env}/server.toml" if mfs_home_env else None,
        str(Path.home() / ".mfs" / "server.toml"),
        "/etc/mfs/server.toml",
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return Path(c)
    return None


def _migrate_legacy_blocks(data: dict[str, Any]) -> None:
    """Auto-migrate legacy toml schemas in place so old configs keep working.

    Two renames happened together:

    1. [metadata] backend/dsn + [transformation_cache] backend/dsn were
       unified into [database] backend/dsn. If the new block is missing but
       a legacy backend/dsn is set, copy it across so the user doesn't have
       to re-run the wizard.

    2. [object_store] (storage backend) + [artifact_cache] (size policy)
       were merged into [artifact_cache] (storage + policy in one block).
       Move object_store keys into artifact_cache; preserve any existing
       artifact_cache policy fields.
    """
    # 1. [database] from legacy [metadata]/[transformation_cache]
    if "database" not in data:
        legacy_backend = None
        legacy_dsn = None
        for src in ("metadata", "transformation_cache"):
            blk = data.get(src) or {}
            if blk.get("backend") and not legacy_backend:
                legacy_backend = blk["backend"]
            if blk.get("dsn") and not legacy_dsn:
                legacy_dsn = blk["dsn"]
        if legacy_backend or legacy_dsn:
            data["database"] = {}
            if legacy_backend:
                data["database"]["backend"] = legacy_backend
            if legacy_dsn:
                data["database"]["dsn"] = legacy_dsn

    # 2. [artifact_cache] from legacy [object_store] + old [artifact_cache] policy
    legacy_os = data.pop("object_store", None)
    if legacy_os:
        merged = dict(data.get("artifact_cache") or {})
        # Old [artifact_cache] (policy) keys take precedence — they were the
        # explicit value; storage fields from [object_store] fill in the rest.
        for k, v in legacy_os.items():
            merged.setdefault(k, v)
        data["artifact_cache"] = merged


def _apply_env_overrides(cfg: ServerConfig) -> None:
    """Env overrides for dogfood: Milvus endpoint/token from env if not set in toml.

    MFS_MILVUS_URI / MFS_MILVUS_TOKEN take precedence; falls back to ZILLIZ_URI /
    ZILLIZ_API_KEY when those are set (so the existing Zilliz creds work out of the box).
    OpenAI key is read by the openai SDK directly from OPENAI_API_KEY.
    """
    api_token = os.environ.get("MFS_API_TOKEN")
    if api_token:
        cfg.auth_token = api_token

    summ = os.environ.get("MFS_SUMMARY_ENABLED")
    if summ is not None:
        cfg.summary.enabled = summ.strip().lower() in ("1", "true", "yes", "on")

    uri = os.environ.get("MFS_MILVUS_URI") or os.environ.get("ZILLIZ_URI")
    token = os.environ.get("MFS_MILVUS_TOKEN") or os.environ.get("ZILLIZ_API_KEY")
    if uri:
        cfg.milvus.uri = uri
    if token:
        cfg.milvus.token = token

    # Database (Postgres) backend env override. Sets the unified [database]
    # block; resolve_defaults already ran by this point so we also push the
    # value into the per-store configs.
    meta_dsn = os.environ.get("MFS_METADATA_DSN")
    if meta_dsn:
        cfg.database.backend = "postgres"
        cfg.database.dsn = meta_dsn
        cfg.metadata.backend = "postgres"
        cfg.metadata.dsn = meta_dsn
    tx_dsn = os.environ.get("MFS_TX_CACHE_DSN") or meta_dsn
    if tx_dsn and os.environ.get("MFS_TX_CACHE_PG"):  # opt-in: share PG for tx cache
        cfg.transformation_cache.backend = "postgres"
        cfg.transformation_cache.dsn = tx_dsn

    # Artifact cache (S3-class) backend env overrides.
    bucket = os.environ.get("MFS_OBJECT_STORE_BUCKET")
    if bucket:
        cfg.artifact_cache.backend = "s3"
        cfg.artifact_cache.bucket = bucket
        for env_k, attr in (
            ("MFS_OBJECT_STORE_ENDPOINT", "endpoint_url"),
            ("MFS_OBJECT_STORE_REGION", "region"),
            ("MFS_OBJECT_STORE_ACCESS_KEY", "access_key_id"),
            ("MFS_OBJECT_STORE_SECRET_KEY", "secret_access_key"),
            ("MFS_OBJECT_STORE_PREFIX", "prefix"),
        ):
            v = os.environ.get(env_k)
            if v:
                setattr(cfg.artifact_cache, attr, v)


def load_server_config(explicit: str | None = None, apply_env: bool = True) -> ServerConfig:
    path = _find_config_path(explicit)
    data: dict = {}
    if path:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    _migrate_legacy_blocks(data)
    cfg = ServerConfig(**data)
    cfg.resolve_defaults()
    if apply_env:
        _apply_env_overrides(cfg)
    return cfg
