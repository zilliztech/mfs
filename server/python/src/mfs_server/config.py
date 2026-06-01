"""Server configuration: load from server.toml (lookup chain) + env overrides.

Lookup order: --config arg -> $MFS_SERVER_CONFIG -> ./server.toml
-> ~/.mfs/server.toml -> /etc/mfs/server.toml -> built-in defaults.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from pydantic import BaseModel

if sys.version_info < (3, 11):
    import tomli as tomllib
else:
    import tomllib


def mfs_home() -> Path:
    home = Path(os.environ.get("MFS_HOME", str(Path.home() / ".mfs")))
    home.mkdir(parents=True, exist_ok=True)
    return home


class MetadataConfig(BaseModel):
    backend: str = "sqlite"  # sqlite | postgres
    path: str = ""  # sqlite file (default ~/.mfs/metadata.db)
    dsn: str = ""  # postgres DSN (env-resolvable)


class ObjectStoreConfig(BaseModel):
    backend: str = "local"  # local | s3 (covers R2/GCS/MinIO via endpoint_url)
    root: str = ""  # local root (default ~/.mfs/cache)
    # s3 backend (also R2/GCS/MinIO via endpoint_url)
    bucket: str = ""
    prefix: str = "mfs"
    endpoint_url: str = ""  # set for R2/GCS/MinIO; empty = AWS
    region: str = "us-east-1"
    access_key_id: str = ""
    secret_access_key: str = ""


class MilvusConfig(BaseModel):
    uri: str = ""  # ~/.mfs/milvus.db (Lite) | https://*.zillizcloud.com
    token: str = ""
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


class TransformationCacheConfig(BaseModel):
    enabled: bool = True
    backend: str = "sqlite"  # sqlite | postgres
    db_path: str = ""  # default ~/.mfs/transformation_cache.db
    dsn: str = ""
    max_size_gb: float = 5.0
    eviction_interval_s: int = 600
    write_flush_interval_s: float = 2.0
    write_buffer_max: int = 5000
    lookup_batch_size: int = 1000


class ArtifactCacheConfig(BaseModel):
    max_size_gb: float = 10.0
    eviction: str = "lru"


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
    metadata: MetadataConfig = MetadataConfig()
    object_store: ObjectStoreConfig = ObjectStoreConfig()
    milvus: MilvusConfig = MilvusConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
    summary: SummaryConfig = SummaryConfig()
    vlm: VlmConfig = VlmConfig()
    converter: ConverterConfig = ConverterConfig()
    transformation_cache: TransformationCacheConfig = TransformationCacheConfig()
    artifact_cache: ArtifactCacheConfig = ArtifactCacheConfig()
    worker: WorkerConfig = WorkerConfig()
    chunk: ChunkConfig = ChunkConfig()
    search: SearchConfig = SearchConfig()

    def resolve_defaults(self) -> "ServerConfig":
        home = Path(self.home) if self.home else mfs_home()
        home.mkdir(parents=True, exist_ok=True)
        self.home = str(home)
        if not self.metadata.path:
            self.metadata.path = str(home / "metadata.db")
        if not self.object_store.root:
            self.object_store.root = str(home / "cache")
        if not self.milvus.uri:
            self.milvus.uri = str(home / "milvus.db")  # Lite default
        if not self.transformation_cache.db_path:
            self.transformation_cache.db_path = str(home / "transformation_cache.db")
        return self


def _find_config_path(explicit: str | None) -> Path | None:
    candidates = [
        explicit,
        os.environ.get("MFS_SERVER_CONFIG"),
        "./server.toml",
        str(Path.home() / ".mfs" / "server.toml"),
        "/etc/mfs/server.toml",
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return Path(c)
    return None


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

    # metadata / transformation_cache Postgres backend (CS / multi-replica)
    meta_dsn = os.environ.get("MFS_METADATA_DSN")
    if meta_dsn:
        cfg.metadata.backend = "postgres"
        cfg.metadata.dsn = meta_dsn
    tx_dsn = os.environ.get("MFS_TX_CACHE_DSN") or meta_dsn
    if tx_dsn and os.environ.get("MFS_TX_CACHE_PG"):  # opt-in: share PG for tx cache
        cfg.transformation_cache.backend = "postgres"
        cfg.transformation_cache.dsn = tx_dsn

    # object store: S3 / R2 / GCS / MinIO
    bucket = os.environ.get("MFS_OBJECT_STORE_BUCKET")
    if bucket:
        cfg.object_store.backend = "s3"
        cfg.object_store.bucket = bucket
        for env_k, attr in (
            ("MFS_OBJECT_STORE_ENDPOINT", "endpoint_url"),
            ("MFS_OBJECT_STORE_REGION", "region"),
            ("MFS_OBJECT_STORE_ACCESS_KEY", "access_key_id"),
            ("MFS_OBJECT_STORE_SECRET_KEY", "secret_access_key"),
            ("MFS_OBJECT_STORE_PREFIX", "prefix"),
        ):
            v = os.environ.get(env_k)
            if v:
                setattr(cfg.object_store, attr, v)


def load_server_config(explicit: str | None = None, apply_env: bool = True) -> ServerConfig:
    path = _find_config_path(explicit)
    data: dict = {}
    if path:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    cfg = ServerConfig(**data)
    cfg.resolve_defaults()
    if apply_env:
        _apply_env_overrides(cfg)
    return cfg
