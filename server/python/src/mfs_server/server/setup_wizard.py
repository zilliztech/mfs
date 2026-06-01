"""Interactive setup wizard — `mfs-server setup`.

Walks the operator through the base server config (embedding / vlm / milvus /
metadata / object_store / auth) one section at a time and writes the result to
$MFS_HOME/server.toml. Every prompt has a "lightweight-local" default so the
operator can press Enter through the whole flow and get a self-contained
server that needs zero external services. Plugging in OpenAI / Zilliz Cloud /
Postgres / S3 is purely opt-in — provide credentials when prompted and the
section flips to the hosted backend.

Connector setup is intentionally NOT here. Connectors are added one at a time
through their own per-scheme flow (mfs-server connector add <uri>), not by
walking through every connector type during base setup.

UI is built on `wizard_ui` (rich panels + questionary prompts). The
underlying TOML schema is unchanged from the pre-styling version.
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path
from typing import Any, Callable

try:
    import tomli_w

    _HAVE_TOMLI_W = True
except ImportError:
    _HAVE_TOMLI_W = False

from ..config import (
    EmbeddingConfig,
    MetadataConfig,
    MilvusConfig,
    ObjectStoreConfig,
    ServerConfig,
    SummaryConfig,
    VlmConfig,
    load_server_config,
    mfs_home,
)
from . import wizard_ui as ui

SECTIONS = ("embedding", "vlm", "milvus", "metadata", "object_store", "auth")


# ─── per-section wizards ────────────────────────────────────────────────────


def _embedding_choices() -> list[tuple[str, str]]:
    return [
        ("onnx", "local, no API key (default)"),
        ("openai", "needs OPENAI_API_KEY env"),
        ("gemini", "needs `uv sync --extra gemini`"),
        ("voyage", "needs `uv sync --extra voyage`"),
        ("ollama", "needs `uv sync --extra ollama` + running ollama server"),
        ("local", "needs `uv sync --extra local` (pulls torch ~2 GB)"),
    ]


def _llm_choices() -> list[tuple[str, str]]:
    return [
        ("openai", "needs OPENAI_API_KEY env"),
        ("anthropic", "needs `uv sync --extra anthropic` + ANTHROPIC_API_KEY"),
        ("gemini", "needs `uv sync --extra gemini` + GOOGLE_API_KEY"),
    ]


def _wizard_embedding(current: EmbeddingConfig, step: int, total: int) -> dict[str, Any]:
    from ..common.embeddings import DEFAULT_MODELS as EMBED_DEFAULTS

    ui.section(
        "Embedding",
        "Default is local ONNX (no API key, multilingual BGE-M3 int8, ~600 MB on\n"
        "first use). Pick another provider to opt out.",
        step=step,
        total=total,
    )
    provider = ui.select(
        "Provider",
        _embedding_choices(),
        default=current.provider if current.provider else "onnx",
    )
    default_model = (
        current.model
        if current.provider == provider and current.model
        else EMBED_DEFAULTS.get(provider, "")
    )
    model = ui.text("Model", default=default_model, required=True)
    dim_hints = {
        "openai": {
            "text-embedding-3-small": 1536,
            "text-embedding-3-large": 3072,
            "text-embedding-ada-002": 1536,
        },
        "onnx": {"gpahal/bge-m3-onnx-int8": 1024, "Xenova/bge-small-en-v1.5": 384},
        "gemini": {"gemini-embedding-001": 768},
        "voyage": {"voyage-3-lite": 512, "voyage-3": 1024, "voyage-3-large": 2048},
        "ollama": {"nomic-embed-text": 768, "mxbai-embed-large": 1024},
        "local": {"all-MiniLM-L6-v2": 384, "all-mpnet-base-v2": 768},
    }
    dim_default = (
        dim_hints.get(provider, {}).get(model)
        or (current.dim if current.provider == provider else 0)
        or 1024
    )
    dim = ui.int_text("Dimension", default=int(dim_default), min_v=1, max_v=8192)

    if provider == "openai":
        ui.info("OPENAI_API_KEY is read from env at request time, not stored here.")
    elif provider != "onnx":
        ui.info(f"Provider {provider!r} needs: uv sync --extra {provider}")
    return {"provider": provider, "model": model, "dim": dim}


def _wizard_vlm(
    current_summary: SummaryConfig, current_vlm: VlmConfig, step: int, total: int
) -> dict[str, Any]:
    from ..common.llm import DEFAULT_VISION_MODELS as VISION_DEFAULTS

    ui.section(
        "Image summary / VLM",
        "When ON, the server generates a text description for each image (uses an\n"
        "LLM, needs an API key, costs $$). OFF by default — image objects are\n"
        "listed but not embedded.",
        step=step,
        total=total,
    )
    enabled = ui.confirm("Enable image summary?", default=current_summary.enabled)
    if not enabled:
        return {"summary_enabled": False}
    provider = ui.select(
        "VLM provider",
        _llm_choices(),
        default=current_vlm.provider if current_vlm.provider else "openai",
    )
    model_default = (
        current_vlm.model
        if current_vlm.provider == provider and current_vlm.model
        else VISION_DEFAULTS.get(provider, "")
    )
    model = ui.text("VLM model", default=model_default, required=True)
    if provider != "openai":
        ui.info(f"Provider {provider!r} needs: uv sync --extra {provider}")
    return {
        "summary_enabled": True,
        "summary_include_image_desc": True,
        "vlm_provider": provider,
        "vlm_model": model,
    }


def _is_lite_path(uri: str) -> bool:
    """A Lite URI is a local filesystem path (e.g. ~/.mfs/milvus.db) — never a
    sensible default to surface when the user is configuring a *remote* Milvus."""
    return bool(uri) and not (uri.startswith("http://") or uri.startswith("https://"))


def _wizard_milvus(
    current: MilvusConfig, *, env_resolved: bool, step: int, total: int
) -> dict[str, Any]:
    ui.section(
        "Milvus (vector DB)",
        "Default = Milvus Lite (a file under $MFS_HOME). Switch to a remote\n"
        "Milvus / Zilliz Cloud by supplying the URI.",
        step=step,
        total=total,
    )
    # Lite is always preselected unless the *prior toml* has an http URI.
    # Env-resolved values are deliberately NOT surfaced as defaults — see PR
    # description for why.
    default_backend = "remote" if (current.uri.startswith("http") and not env_resolved) else "lite"
    backend = ui.select(
        "Backend",
        [
            ("lite", "local file under $MFS_HOME (no setup)"),
            ("remote", "Zilliz Cloud / Milvus standalone"),
        ],
        default=default_backend,
    )
    cl = ui.select(
        "Consistency level",
        [
            ("", "Milvus default (Bounded ~5s staleness)"),
            ("Strong", "strict read-your-writes (highest latency)"),
            ("Bounded", "Milvus default explicitly"),
            ("Eventually", "lowest latency, may read stale"),
            ("Session", "per-session consistency"),
        ],
        default=current.consistency_level or "",
    )
    if backend == "lite":
        return {"uri": "", "token": "", "consistency_level": cl}
    # Remote: require a non-empty URI and don't echo a Lite-path default.
    raw_default_uri = "" if env_resolved else current.uri
    if _is_lite_path(raw_default_uri):
        raw_default_uri = ""
    uri = ui.text(
        "URI",
        default=raw_default_uri,
        required=True,
        hint="e.g. https://xxx.zillizcloud.com",
        validate=lambda v: (
            None
            if v.startswith(("http://", "https://"))
            else "URI must start with http:// or https://"
        ),
    )
    tok_default = "" if env_resolved else current.token
    token = ui.password("Token", default=tok_default, hint="leave blank if your Milvus has no auth")
    if env_resolved and not uri:
        ui.info("(kept empty — server will fall back to $MFS_MILVUS_URI / $ZILLIZ_URI at runtime.)")
    return {"uri": uri, "token": token, "consistency_level": cl}


def _wizard_metadata(current: MetadataConfig, step: int, total: int) -> dict[str, Any]:
    ui.section(
        "Metadata DB",
        "Default = SQLite (a file under $MFS_HOME). Switch to Postgres for\n"
        "team / multi-replica deployments.",
        step=step,
        total=total,
    )
    backend = ui.select(
        "Backend",
        [
            ("sqlite", "single-host, file-based (no setup)"),
            ("postgres", "asyncpg-backed (needs Postgres reachable)"),
        ],
        default=current.backend,
    )
    if backend == "sqlite":
        return {"backend": "sqlite", "dsn": ""}
    dsn = ui.password(
        "DSN",
        default=current.dsn,
        required=True,
        hint="postgresql://user:pass@host:5432/db",
    )
    return {"backend": "postgres", "dsn": dsn}


def _wizard_object_store(current: ObjectStoreConfig, step: int, total: int) -> dict[str, Any]:
    ui.section(
        "Object store",
        "Holds transformation-cache artifacts (PDF→markdown, VLM descriptions).\n"
        "Default = local filesystem under $MFS_HOME. Switch to S3 (or MinIO /\n"
        "R2 / GCS via endpoint_url) for shared storage across server replicas.",
        step=step,
        total=total,
    )
    backend = ui.select(
        "Backend",
        [
            ("local", "filesystem under $MFS_HOME (no setup)"),
            ("s3", "S3 / MinIO / R2 / GCS via endpoint_url"),
        ],
        default=current.backend,
    )
    if backend == "local":
        return {"backend": "local"}
    bucket = ui.text("Bucket", default=current.bucket, required=True)
    endpoint_url = ui.text(
        "Endpoint URL",
        default=current.endpoint_url,
        hint="leave blank for AWS S3 / set for MinIO / R2 / GCS",
    )
    region = ui.text("Region", default=current.region or "us-east-1")
    access_key = ui.password("Access key id", default=current.access_key_id, required=True)
    secret_key = ui.password("Secret access key", default=current.secret_access_key, required=True)
    return {
        "backend": "s3",
        "bucket": bucket,
        "endpoint_url": endpoint_url,
        "region": region,
        "access_key_id": access_key,
        "secret_access_key": secret_key,
    }


def _wizard_auth(current_token: str, step: int, total: int) -> dict[str, Any]:
    ui.section(
        "API authentication",
        "Clients authenticate via Bearer token. Pick a mode:",
        step=step,
        total=total,
    )
    mode = ui.select(
        "Mode",
        [
            ("auto", "auto-generate a random token (recommended)"),
            ("custom", "use a specific token I provide"),
            ("disable", "no auth (loopback-only deployments)"),
        ],
        default="custom"
        if current_token and current_token != "-"
        else ("disable" if current_token == "-" else "auto"),
    )
    if mode == "auto":
        return {"auto": True}
    if mode == "disable":
        return {"token": "-"}
    tok = ui.text("Token", default=current_token if current_token != "-" else "", required=True)
    return {"token": tok}


# ─── apply + render ─────────────────────────────────────────────────────────


def _build_runners(
    milvus_from_env: bool,
    *,
    step_of: dict[str, int],
    total: int,
) -> dict[str, Callable[[ServerConfig], dict[str, Any]]]:
    return {
        "embedding": lambda c: _wizard_embedding(c.embedding, step_of["embedding"], total),
        "vlm": lambda c: _wizard_vlm(c.summary, c.vlm, step_of["vlm"], total),
        "milvus": lambda c: _wizard_milvus(
            c.milvus, env_resolved=milvus_from_env, step=step_of["milvus"], total=total
        ),
        "metadata": lambda c: _wizard_metadata(c.metadata, step_of["metadata"], total),
        "object_store": lambda c: _wizard_object_store(
            c.object_store, step_of["object_store"], total
        ),
        "auth": lambda c: _wizard_auth(c.auth_token, step_of["auth"], total),
    }


def _apply(section: str, current: dict[str, Any], answers: dict[str, Any]) -> dict[str, Any]:
    """Merge wizard answers into the running TOML dict."""
    if section == "embedding":
        current["embedding"] = {
            "provider": answers["provider"],
            "model": answers["model"],
            "dim": answers["dim"],
        }
    elif section == "vlm":
        # `vlm` toggles summary.enabled (master switch + LLM for text summary)
        # and the vlm.* block (LLM for image descriptions). They share the same
        # provider/model by default — multimodal LLMs handle both. Users who
        # want different LLMs can hand-edit summary.* separately.
        summ = current.setdefault("summary", {})
        summ["enabled"] = answers["summary_enabled"]
        if answers["summary_enabled"]:
            summ["include_image_desc"] = answers.get("summary_include_image_desc", True)
            summ["provider"] = answers["vlm_provider"]
            summ["model"] = answers["vlm_model"]
            current["vlm"] = {"provider": answers["vlm_provider"], "model": answers["vlm_model"]}
    elif section == "milvus":
        # consistency_level can be empty string (= Milvus SDK default); keep
        # it explicitly when set; drop when blank.
        block = {k: v for k, v in answers.items() if v != ""}
        if block:
            current["milvus"] = block
        else:
            current.pop("milvus", None)
    elif section == "metadata":
        block = {k: v for k, v in answers.items() if v != ""}
        current["metadata"] = block
    elif section == "object_store":
        block = {k: v for k, v in answers.items() if v != ""}
        current["object_store"] = block
    elif section == "auth":
        if answers.get("auto"):
            current.pop("auth_token", None)  # let server auto-generate to server.token
        else:
            current["auth_token"] = answers["token"]
    return current


def _load_existing_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import tomllib  # py3.11+
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]
    with open(path, "rb") as f:
        return tomllib.load(f)


def _write_toml(path: Path, data: dict[str, Any]) -> None:
    # Drop any subtable that ended up empty so we don't emit `[milvus]\n` with
    # nothing under it.
    cleaned = {k: v for k, v in data.items() if not (isinstance(v, dict) and not v)}
    if not _HAVE_TOMLI_W:
        path.write_text(_render_toml(cleaned))
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        tomli_w.dump(cleaned, f)


def _render_toml(data: dict[str, Any], _parent: str = "") -> str:
    lines: list[str] = []
    for k, v in data.items():
        if isinstance(v, dict):
            continue
        lines.append(f"{k} = {_format_scalar(v)}")
    for k, v in data.items():
        if not isinstance(v, dict):
            continue
        table_name = f"{_parent}.{k}" if _parent else k
        lines.append("")
        lines.append(f"[{table_name}]")
        for k2, v2 in v.items():
            lines.append(f"{k2} = {_format_scalar(v2)}")
    return "\n".join(lines) + "\n"


def _format_scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def _bootstrap_auth_token(token_dir: Path) -> str:
    token_dir.mkdir(parents=True, exist_ok=True)
    token_file = token_dir / "server.token"
    if token_file.exists():
        return token_file.read_text().strip()
    tok = secrets.token_urlsafe(32)
    token_file.write_text(tok)
    try:
        token_file.chmod(0o600)
    except OSError:
        pass
    return tok


# ─── final summary ─────────────────────────────────────────────────────────


def _summary_pairs(out: dict[str, Any]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    e = out.get("embedding", {})
    if e:
        pairs.append(("Embedding", f"{e.get('provider')} / {e.get('model')} (dim={e.get('dim')})"))
    s = out.get("summary", {})
    if s.get("enabled"):
        v = out.get("vlm", {})
        pairs.append(("VLM", f"{v.get('provider', '?')} / {v.get('model', '?')}"))
    else:
        pairs.append(("VLM", "off"))
    m = out.get("milvus", {})
    if m.get("uri", "").startswith("http"):
        pairs.append(("Milvus", f"remote {m.get('uri')}"))
    else:
        pairs.append(("Milvus", "Lite (local file)"))
    if m.get("consistency_level"):
        pairs.append(("  consistency", m["consistency_level"]))
    md = out.get("metadata", {})
    pairs.append(("Metadata", md.get("backend", "sqlite")))
    obj = out.get("object_store", {})
    pairs.append(("Object store", obj.get("backend", "local")))
    if "auth_token" in out:
        pairs.append(("Auth", "disabled" if out["auth_token"] == "-" else "custom token"))
    else:
        pairs.append(("Auth", "auto-generated token"))
    return pairs


# ─── runner ─────────────────────────────────────────────────────────────────


def run_wizard(sections: list[str] | None = None, config_path: str | None = None) -> int:
    """Drive the wizard end-to-end.

    sections=None or empty → walk every section in declaration order.
    sections=["embedding"] → only that section is edited; existing values in
                              the other sections are preserved.
    """
    target_sections = list(sections) if sections else list(SECTIONS)
    unknown = [s for s in target_sections if s not in SECTIONS]
    if unknown:
        print(f"unknown section(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"valid: {', '.join(SECTIONS)}", file=sys.stderr)
        return 2

    out_path = Path(config_path) if config_path else (mfs_home() / "server.toml")
    if not config_path:
        os.environ.setdefault("MFS_HOME", str(mfs_home()))

    existing = _load_existing_toml(out_path)
    current_resolved = load_server_config(str(out_path) if out_path.exists() else None)
    milvus_from_env = bool(
        (os.environ.get("MFS_MILVUS_URI") or os.environ.get("ZILLIZ_URI"))
        and not existing.get("milvus", {}).get("uri")
    )

    total = len(target_sections)
    step_of = {sect: i + 1 for i, sect in enumerate(target_sections)}
    runners = _build_runners(milvus_from_env, step_of=step_of, total=total)

    ui.clear()
    ui.banner(
        "MFS server setup",
        lines=[
            f"writing to {out_path}",
            f"{total} section(s): {', '.join(target_sections)}",
            "Press Ctrl-C to abort without saving",
        ],
    )

    try:
        for sect in target_sections:
            answers = runners[sect](current_resolved)
            existing = _apply(sect, existing, answers)
    except KeyboardInterrupt:
        ui.warn("aborted; nothing written.")
        return 130

    if out_path.exists():
        bak = out_path.with_suffix(out_path.suffix + ".bak")
        bak.write_bytes(out_path.read_bytes())
        ui.note(f"backed up previous config to {bak}")
    _write_toml(out_path, existing)
    ui.emphasis(f"wrote {out_path}")

    if "auth_token" not in existing:
        tok = _bootstrap_auth_token(out_path.parent)
        ui.note(f"\nAPI token written to {out_path.parent / 'server.token'}")
        ui.note("  Pass to clients on other machines via:")
        ui.emphasis(f"    export MFS_API_TOKEN={tok}")

    ui.console.print()
    ui.console.print("[bold #5fafff]Summary[/bold #5fafff]")
    ui.list_kv(_summary_pairs(existing))
    ui.note("\nStart the server with:")
    ui.emphasis("  mfs-server run")
    return 0


def main_entry(argv: list[str]) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="mfs-server setup", description=__doc__.splitlines()[0])
    p.add_argument(
        "--section",
        action="append",
        choices=SECTIONS,
        help="Limit the wizard to one or more sections; repeat for several. "
        "Default (no --section) walks every section.",
    )
    p.add_argument(
        "--config",
        default=None,
        help="Path to write server.toml (default: $MFS_HOME/server.toml)",
    )
    args = p.parse_args(argv)
    return run_wizard(sections=args.section, config_path=args.config)
