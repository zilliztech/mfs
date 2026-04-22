"""Global configuration management.

Loads from ~/.mfs/config.toml, provides defaults for all settings.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib  # type: ignore[attr-defined]
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]


def _mfs_home() -> Path:
    return Path(os.environ.get("MFS_HOME", str(Path.home() / ".mfs")))


# Module-level binding kept for backwards-compat with code that imports
# ``MFS_HOME`` directly. Reloading the module re-evaluates the env var.
MFS_HOME = _mfs_home()


@dataclass
class EmbeddingConfig:
    provider: str = "openai"  # openai | onnx | sentence-transformers
    model: str = "text-embedding-3-small"
    dimension: int = 1536
    api_key: str | None = None
    batch_size: int = 32


@dataclass
class LLMConfig:
    provider: str = "openai"  # openai | anthropic | google | ollama | mistral
    model: str = ""           # empty -> use llm.DEFAULT_MODELS[provider]
    api_key: str = ""
    base_url: str = ""


@dataclass
class IndexingConfig:
    include_extensions: list[str] = field(default_factory=list)
    exclude_extensions: list[str] = field(default_factory=list)


@dataclass
class CacheConfig:
    max_size_mb: int = 500


@dataclass
class MilvusConfig:
    uri: str = ""
    collection_name: str = "mfs_chunks"
    account_id: str = "default"
    token: str = ""

    def __post_init__(self) -> None:
        if not self.uri:
            self.uri = str(_mfs_home() / "milvus.db")


@dataclass
class Config:
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    indexing: IndexingConfig = field(default_factory=IndexingConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    milvus: MilvusConfig = field(default_factory=MilvusConfig)

    @property
    def mfs_home(self) -> Path:
        return _mfs_home()


def ensure_mfs_home() -> Path:
    """Create ~/.mfs/ (and a default ~/.mfs/config.toml) if missing.

    On first run we drop a heavily-commented template at ``config.toml`` and
    print a one-line stderr notice so users discover the file. The notice
    goes to stderr to avoid polluting ``--json`` output on stdout.
    """
    home = _mfs_home()
    try:
        home.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(
            f"cannot create MFS home directory {home}: {exc}"
        ) from exc
    (home / "converted").mkdir(exist_ok=True)

    cfg = home / "config.toml"
    if not cfg.exists():
        try:
            cfg.write_text(default_config_template(), encoding="utf-8")
            print(f"note: created default config at {cfg}", file=sys.stderr)
        except OSError as exc:
            print(
                f"warning: could not write default config at {cfg}: {exc}",
                file=sys.stderr,
            )
    return home


def _config_path() -> Path:
    return _mfs_home() / "config.toml"


def config_path() -> Path:
    """Public accessor used by the CLI ``mfs config path`` command."""
    return _config_path()


def default_config_template() -> str:
    """Return the heavily-commented default config.toml as a string.

    All keys are commented out so the file documents itself without
    overriding the in-code defaults until the user uncomments a line.
    """
    return _DEFAULT_CONFIG_TEMPLATE


_DEFAULT_CONFIG_TEMPLATE = """\
# MFS — Semantic File Search configuration.
#
# All keys below are commented out and show the built-in defaults.
# Uncomment any line to override. Environment variables (e.g. OPENAI_API_KEY)
# overlay any [embedding].api_key / [llm].api_key left empty here.

[embedding]
# provider   = "openai"                   # openai | onnx | jina | voyage | google | mistral | ollama | local
# model      = "text-embedding-3-small"   # provider-specific model id
# dimension  = 1536                       # auto-aligned for known openai/onnx models
# api_key    = ""                         # leave empty to use OPENAI_API_KEY (or provider-specific env)
# batch_size = 32                         # chunks per embedding request

[llm]
# provider = "openai"                     # openai | anthropic | google | ollama | mistral
# model    = ""                           # empty -> provider default
# api_key  = ""                           # leave empty to use *_API_KEY env vars
# base_url = ""                           # optional override, e.g. local Ollama / proxy

[indexing]
# include_extensions = []                 # restrict to these extensions if non-empty
# exclude_extensions = []                 # always skip these extensions

[cache]
# max_size_mb = 500                       # converted-file cache cap

[milvus]
# uri             = "<MFS_HOME>/milvus.db"  # default: Milvus Lite file under ~/.mfs/
# collection_name = "mfs_chunks"
# account_id      = "default"
# token           = ""
"""


def load_config() -> Config:
    """Load config from ~/.mfs/config.toml, falling back to defaults.

    Environment variable OPENAI_API_KEY (if set) is applied to the OpenAI
    provider regardless of whether it is written into config.toml.
    """
    ensure_mfs_home()
    cfg = Config()

    path = _config_path()
    if path.exists():
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
        _apply_toml(cfg, data)

    # ENV overrides
    if cfg.embedding.provider == "openai" and not cfg.embedding.api_key:
        cfg.embedding.api_key = os.environ.get("OPENAI_API_KEY")
    if cfg.llm.provider == "openai" and not cfg.llm.api_key:
        cfg.llm.api_key = os.environ.get("OPENAI_API_KEY", "")
    if cfg.llm.provider == "anthropic" and not cfg.llm.api_key:
        cfg.llm.api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if cfg.llm.provider == "google" and not cfg.llm.api_key:
        cfg.llm.api_key = (
            os.environ.get("GOOGLE_API_KEY")
            or os.environ.get("GEMINI_API_KEY")
            or ""
        )
    if cfg.embedding.provider == "google" and not cfg.embedding.api_key:
        cfg.embedding.api_key = (
            os.environ.get("GOOGLE_API_KEY")
            or os.environ.get("GEMINI_API_KEY")
            or ""
        )
    if cfg.llm.provider == "mistral" and not cfg.llm.api_key:
        cfg.llm.api_key = os.environ.get("MISTRAL_API_KEY", "")

    # Align dimension with known model defaults if user didn't override
    if cfg.embedding.provider == "openai" and cfg.embedding.model == "text-embedding-3-small":
        cfg.embedding.dimension = 1536
    elif cfg.embedding.provider == "openai" and cfg.embedding.model == "text-embedding-3-large":
        cfg.embedding.dimension = 3072
    elif cfg.embedding.provider == "onnx":
        cfg.embedding.dimension = 1024  # bge-m3

    return cfg


def _apply_toml(cfg: Config, data: dict) -> None:
    emb = data.get("embedding", {})
    if "provider" in emb:
        cfg.embedding.provider = emb["provider"]
    if "model" in emb:
        cfg.embedding.model = emb["model"]
    if "dimension" in emb:
        cfg.embedding.dimension = int(emb["dimension"])
    if "api_key" in emb:
        cfg.embedding.api_key = emb["api_key"]
    if "batch_size" in emb:
        cfg.embedding.batch_size = int(emb["batch_size"])

    llm = data.get("llm", {})
    if "provider" in llm:
        cfg.llm.provider = llm["provider"]
    if "model" in llm:
        cfg.llm.model = llm["model"]
    if "api_key" in llm:
        cfg.llm.api_key = llm["api_key"]
    if "base_url" in llm:
        cfg.llm.base_url = llm["base_url"]

    idx = data.get("indexing", {})
    if "include_extensions" in idx:
        cfg.indexing.include_extensions = list(idx["include_extensions"])
    if "exclude_extensions" in idx:
        cfg.indexing.exclude_extensions = list(idx["exclude_extensions"])

    cache = data.get("cache", {})
    if "max_size_mb" in cache:
        cfg.cache.max_size_mb = int(cache["max_size_mb"])

    mv = data.get("milvus", {})
    if "uri" in mv:
        cfg.milvus.uri = mv["uri"]
    if "collection_name" in mv:
        cfg.milvus.collection_name = mv["collection_name"]
    if "account_id" in mv:
        cfg.milvus.account_id = mv["account_id"]
    if "token" in mv:
        cfg.milvus.token = mv["token"]


# ---------------------------------------------------------------------------
# Helpers used by ``mfs config show / get / set``
# ---------------------------------------------------------------------------


# Maps dotted key -> (section, field, kind) where kind is "str"|"int"|"list".
_KEY_SCHEMA: dict[str, tuple[str, str, str]] = {
    "embedding.provider":   ("embedding", "provider",   "str"),
    "embedding.model":      ("embedding", "model",      "str"),
    "embedding.dimension":  ("embedding", "dimension",  "int"),
    "embedding.api_key":    ("embedding", "api_key",    "str"),
    "embedding.batch_size": ("embedding", "batch_size", "int"),
    "llm.provider":         ("llm", "provider", "str"),
    "llm.model":            ("llm", "model",    "str"),
    "llm.api_key":          ("llm", "api_key",  "str"),
    "llm.base_url":         ("llm", "base_url", "str"),
    "indexing.include_extensions": ("indexing", "include_extensions", "list"),
    "indexing.exclude_extensions": ("indexing", "exclude_extensions", "list"),
    "cache.max_size_mb":    ("cache",  "max_size_mb",   "int"),
    "milvus.uri":           ("milvus", "uri",            "str"),
    "milvus.collection_name": ("milvus", "collection_name", "str"),
    "milvus.account_id":    ("milvus", "account_id",     "str"),
    "milvus.token":         ("milvus", "token",          "str"),
}

# Maps dotted key -> env var name that overlays it (used for show annotations).
_ENV_OVERLAYS: dict[str, str] = {
    "embedding.api_key": "OPENAI_API_KEY",
    "llm.api_key": "OPENAI_API_KEY",  # fallback; refined by provider in show
}


def known_keys() -> list[str]:
    return list(_KEY_SCHEMA.keys())


def get_value(cfg: Config, dotted_key: str):
    if dotted_key not in _KEY_SCHEMA:
        raise KeyError(dotted_key)
    section, fname, _ = _KEY_SCHEMA[dotted_key]
    return getattr(getattr(cfg, section), fname)


def coerce_value(dotted_key: str, raw: str):
    if dotted_key not in _KEY_SCHEMA:
        raise KeyError(dotted_key)
    _, _, kind = _KEY_SCHEMA[dotted_key]
    if kind == "int":
        return int(raw)
    if kind == "list":
        # Accept either a JSON array (``'["py","md"]'``) or a comma-separated
        # string (``"py,md"``). The JSON form is the authoritative shape —
        # treating the raw string as CSV turns a literal ``'["py","md"]'``
        # into the garbage list ``['["py"', '"md"]']``, which then round-trips
        # through TOML and mangles the config file.
        stripped = raw.strip()
        if stripped.startswith("["):
            import json as _json
            try:
                parsed = _json.loads(stripped)
            except _json.JSONDecodeError as exc:
                raise ValueError(
                    f"list value must be a JSON array or comma-separated "
                    f"string; got {raw!r}: {exc.msg}"
                ) from exc
            if not isinstance(parsed, list):
                raise ValueError(
                    f"list value must decode to a JSON array; got {type(parsed).__name__}"
                )
            return [str(item).strip() for item in parsed if str(item).strip()]
        return [s.strip() for s in raw.split(",") if s.strip()]
    return raw


def file_overrides() -> dict:
    """Return the raw dict parsed from config.toml (or {} if missing/empty)."""
    p = _config_path()
    if not p.exists():
        return {}
    try:
        with open(p, "rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def env_source_for(dotted_key: str, cfg: Config) -> str | None:
    """If the value at *dotted_key* came from an env var, return its name."""
    if dotted_key == "embedding.api_key":
        if cfg.embedding.provider == "openai" and os.environ.get("OPENAI_API_KEY"):
            return "OPENAI_API_KEY"
        if cfg.embedding.provider == "google":
            if os.environ.get("GOOGLE_API_KEY"):
                return "GOOGLE_API_KEY"
            if os.environ.get("GEMINI_API_KEY"):
                return "GEMINI_API_KEY"
    if dotted_key == "llm.api_key":
        provider = cfg.llm.provider
        if provider == "google":
            if os.environ.get("GOOGLE_API_KEY"):
                return "GOOGLE_API_KEY"
            if os.environ.get("GEMINI_API_KEY"):
                return "GEMINI_API_KEY"
            return None
        env_name = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "mistral": "MISTRAL_API_KEY",
        }.get(provider)
        if env_name and os.environ.get(env_name):
            return env_name
    return None


def write_config(data: dict) -> Path:
    """Persist *data* as TOML to ~/.mfs/config.toml.

    Pragmatic note: this loses any hand-authored comments. ``mfs config init``
    can regenerate the commented template; for routine ``set`` calls we accept
    the loss because the canonical defaults are documented in the template.
    """
    import tomli_w

    p = _config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as fh:
        tomli_w.dump(data, fh)
    return p
