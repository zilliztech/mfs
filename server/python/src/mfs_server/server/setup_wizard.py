"""Interactive setup wizard — `mfs-server setup`.

Walks the operator through the base server config one section at a time and
writes the result to $MFS_HOME/server.toml. Every prompt has a "lightweight-
local" default so the operator can press Enter through the whole flow and get
a self-contained server that needs zero external services. Plugging in
OpenAI / Zilliz Cloud / Postgres / S3 is purely opt-in — provide credentials
when prompted and the section flips to the hosted backend.

Sections (default order):
  embedding -> vlm -> milvus -> database -> cache -> auth -> connectors

`database` configures the single relational backend used for both
metadata (connectors / objects / queue) and the transformation-cache
lookup table — see config.DatabaseConfig.

`cache` configures the artifact half of the outward Cache concept
(design doc §2 + §10.4): a local filesystem or S3-compatible store for
derived blobs (PDF→markdown, VLM image descriptions, …). Maps to
[artifact_cache] in the TOML.

`connectors` is optional and runs the per-scheme `connector add` wizard
in a loop; users can skip it (default No) and add connectors later with
`mfs-server connector add <uri>`.

UI is built on `wizard_ui` (rich panels + questionary prompts).
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
    ArtifactCacheConfig,
    DatabaseConfig,
    EmbeddingConfig,
    MilvusConfig,
    ServerConfig,
    SummaryConfig,
    VlmConfig,
    load_server_config,
    mfs_home,
)
from . import wizard_ui as ui

SECTIONS = ("embedding", "vlm", "milvus", "database", "cache", "auth", "connectors")


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


def _probe_dimension(provider: str, model: str) -> tuple[int | None, str | None]:
    """Instantiate the provider and read its `.dimension` property.

    The provider is the source of truth: each backend already knows how to
    resolve dim either from a tiny built-in lookup (openai/voyage well-known
    models — no network) or from an actual probe (onnx/local/ollama load
    weights; gemini does a trial call). Mirroring that in the wizard via a
    second hand-curated table is duplicate work that goes stale.

    Returns (dim, error). On success error is None; on failure dim is None and
    error is a short human-readable explanation the caller can show.
    """
    from ..common.embeddings import get_provider

    try:
        client = get_provider(provider, model)
    except ImportError as exc:
        return None, f"missing dependency: {exc}"
    except Exception as exc:  # noqa: BLE001 — surface anything to the user
        return None, f"{type(exc).__name__}: {exc}"
    try:
        dim = int(client.dimension)
    except Exception as exc:  # noqa: BLE001
        return None, f"provider returned no dimension: {exc}"
    return dim, None


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

    if provider == "openai":
        ui.info("OPENAI_API_KEY is read from env at request time, not stored here.")
    elif provider != "onnx":
        ui.info(f"Provider {provider!r} needs: uv sync --extra {provider}")

    # Probe the actual provider for the real dimension. For ONNX this triggers
    # the model download on first run (HF Hub shows its own progress bar). For
    # OpenAI/Voyage well-known models this is a pure table lookup (<1ms).
    ui.note("Detecting embedding dimension from the provider…")
    dim, err = _probe_dimension(provider, model)
    if dim is not None:
        ui.emphasis(f"  detected dim={dim}")
        return {"provider": provider, "model": model, "dim": dim}

    # Probe failed — usually because the user's env isn't ready yet (missing
    # extra, API key not exported, ollama not running). Surface the cause and
    # let them either fix env + re-run, or override the dim by hand.
    ui.warn(f"could not probe the provider: {err}")
    ui.note(
        "  Enter the dimension manually below, or Ctrl-C to abort, fix the env,\n"
        "  and re-run `mfs-server setup`."
    )
    dim = ui.int_text("Dimension (manual)", default=1024, min_v=1, max_v=8192, required=True)
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
    # consistency_level is intentionally not prompted: the mfs default (Strong;
    # see MilvusStore._cl_kw) handles the read-after-write demo case, and
    # power users can override via [milvus] consistency_level in the toml.
    if backend == "lite":
        return {"uri": "", "token": ""}
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
    return {"uri": uri, "token": token}


def _wizard_database(current: DatabaseConfig, step: int, total: int) -> dict[str, Any]:
    ui.section(
        "Database",
        "One relational backend for all server state:\n"
        "  • connector registry + object index + the ingest job queue\n"
        "  • the transformation cache lookup table (embeddings, summaries)\n\n"
        "Default = SQLite (a file under $MFS_HOME). Switch to Postgres for\n"
        "team / multi-replica deployments where multiple server processes\n"
        "share the same state.",
        step=step,
        total=total,
    )
    backend = ui.select(
        "Backend",
        [
            ("sqlite", "single-host, file-based (no setup)"),
            ("postgres", "asyncpg-backed (needs Postgres reachable)"),
        ],
        default=current.backend or "sqlite",
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


def _wizard_cache(current: ArtifactCacheConfig, step: int, total: int) -> dict[str, Any]:
    ui.section(
        "Cache",
        "Where derived artifact blobs live (PDF→markdown conversions, VLM\n"
        "image descriptions, …). Default = local filesystem under $MFS_HOME.\n"
        "Switch to S3 (or MinIO / R2 / GCS via endpoint_url) for shared\n"
        "storage across server replicas.\n\n"
        "(Size + eviction policy are advanced knobs — defaults are fine; edit\n"
        "[artifact_cache].max_size_gb / eviction in the TOML to change them.)",
        step=step,
        total=total,
    )
    backend = ui.select(
        "Backend",
        [
            ("local", "filesystem under $MFS_HOME (no setup)"),
            ("s3", "S3 / MinIO / R2 / GCS via endpoint_url"),
        ],
        default=current.backend or "local",
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
    # Custom token uses password() so the secret never appears on screen as it
    # is typed (terminal recordings, screen shares, tmux scrollback).
    tok = ui.password(
        "Token",
        default=current_token if current_token != "-" else "",
        required=True,
        hint="input hidden",
    )
    return {"token": tok}


def _wizard_connectors(step: int, total: int) -> dict[str, Any]:
    """Optional last step: invite the user to add one or more connectors now.

    Defaults to "No" — most operators are still gathering credentials when
    they finish base setup and add connectors later. Saying Yes drops them
    into the same per-scheme flow as `mfs-server connector add <uri>`,
    looped until they say "no more".

    Returns {} (this step doesn't modify the server toml itself — each
    connector writes its own toml under $MFS_HOME/connectors/<alias>.toml).
    """
    from .connector_wizard import list_existing_connectors, prompt_and_add_one

    ui.section(
        "Connectors",
        "(optional) Register one or more data sources now. You can also add\n"
        "them later with: mfs-server connector add <uri>",
        step=step,
        total=total,
    )
    existing = list_existing_connectors()
    if existing:
        ui.note(f"Existing connectors in {mfs_home() / 'connectors'}:")
        for row in existing:
            ui.note(f"  • {row['uri']}")
    else:
        ui.note("No connectors registered yet.")

    if not ui.confirm("Add a connector now?", default=False):
        ui.info("Skipped. Add later with: mfs-server connector add <scheme>://<host>")
        return {}

    added = 0
    while True:
        ok = prompt_and_add_one()
        if ok:
            added += 1
        if not ui.confirm("Add another connector?", default=False):
            break
    ui.emphasis(f"  added {added} connector(s)")
    return {}


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
        "database": lambda c: _wizard_database(c.database, step_of["database"], total),
        "cache": lambda c: _wizard_cache(c.artifact_cache, step_of["cache"], total),
        "auth": lambda c: _wizard_auth(c.auth_token, step_of["auth"], total),
        "connectors": lambda c: _wizard_connectors(step_of["connectors"], total),
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
        block = {k: v for k, v in answers.items() if v != ""}
        if block:
            current["milvus"] = block
        else:
            current.pop("milvus", None)
    elif section == "database":
        block = {k: v for k, v in answers.items() if v != ""}
        current["database"] = block
    elif section == "cache":
        block = {k: v for k, v in answers.items() if v != ""}
        current["artifact_cache"] = block
    elif section == "auth":
        if answers.get("auto"):
            current.pop("auth_token", None)  # let server auto-generate to server.token
        else:
            current["auth_token"] = answers["token"]
    elif section == "connectors":
        # No-op: connectors write to their own per-instance tomls under
        # $MFS_HOME/connectors/, not the server toml.
        pass
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
    db = out.get("database", {})
    pairs.append(("Database", db.get("backend", "sqlite")))
    ac = out.get("artifact_cache", {})
    pairs.append(("Cache", ac.get("backend", "local")))
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
        # Auto mode: bootstrap a fresh random token to server.token (chmod 600).
        # We deliberately do NOT echo the token here — terminal scrollback, tmux
        # capture-pane, and recorded sessions would otherwise leak it. The
        # operator reads it from the file when they need it.
        token_file = out_path.parent / "server.token"
        _bootstrap_auth_token(out_path.parent)
        ui.note(f"\nAPI token written to {token_file} (mode 0600, not shown).")
        ui.note("  Read it on this host with:")
        ui.emphasis(f"    cat {token_file}")
        ui.note("  Or export it inline:")
        ui.emphasis(f'    export MFS_API_TOKEN="$(cat {token_file})"')

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
