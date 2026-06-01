"""Interactive setup wizard — `mfs-server setup`.

Walks the operator through the base server config (embedding / vlm / milvus /
metadata / object_store / auth) one section at a time and writes the result to
$MFS_HOME/server.toml. Every prompt has a "lightweight-local" default so the
operator can press Enter through the whole flow and get a self-contained
server that needs zero external services. Plugging in OpenAI / Zilliz Cloud /
Postgres / S3 is purely opt-in — provide credentials when prompted and the
section flips to the hosted backend.

Connector setup is intentionally NOT here. Connectors are added one at a time
through their own per-scheme flow (mfs-server connector add <uri>
--interactive), not by walking through every scheme during base setup.
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

SECTIONS = ("embedding", "vlm", "milvus", "metadata", "object_store", "auth")


# ─── prompt helpers ─────────────────────────────────────────────────────────


def _prompt(label: str, default: str = "", *, secret: bool = False) -> str:
    """Single-line prompt with a printed default. Returns the user's input
    (stripped); empty input keeps the default."""
    suffix = f" [{default}]" if default else ""
    if secret:
        suffix += " (input hidden)"
    raw = input(f"  {label}{suffix}: ")
    val = raw.strip()
    return val if val else default


def _prompt_choice(label: str, choices: list[str], default: str) -> str:
    options = "/".join(c + ("*" if c == default else "") for c in choices)
    while True:
        val = _prompt(f"{label} ({options})", default).lower()
        if val in choices:
            return val
        print(f"    ! please choose one of: {', '.join(choices)}")


def _prompt_bool(label: str, default: bool) -> bool:
    val = _prompt(label + " (y/n)", "y" if default else "n").lower()
    return val in ("y", "yes", "true", "1", "on")


def _section(title: str, body: str = "") -> None:
    print()
    print(f"── {title} ──")
    if body:
        for line in body.strip().splitlines():
            print(f"  {line}")


# ─── per-section wizards ────────────────────────────────────────────────────


def _wizard_embedding(current: EmbeddingConfig) -> dict[str, Any]:
    _section(
        "Embedding",
        "Default is a local ONNX model — no API key. Multilingual BGE-M3 int8\n"
        "(~600 MB) downloads on first use. Provide an OpenAI key to switch\n"
        "to the hosted API.",
    )
    provider = _prompt_choice("Provider", ["onnx", "openai"], current.provider)
    if provider == "onnx":
        # bge-m3-onnx-int8 is the recommended default (multilingual, 1024-dim,
        # int8-quantized for CPU). Alternatives are shown as suggestions only —
        # the user must also adjust `dim` to match.
        model = _prompt(
            "ONNX model (e.g. gpahal/bge-m3-onnx-int8 multilingual 1024d, "
            "Xenova/bge-small-en-v1.5 English 384d)",
            current.model if current.provider == "onnx" else "gpahal/bge-m3-onnx-int8",
        )
        dim_default = str(current.dim) if current.provider == "onnx" else "1024"
        dim = int(_prompt("Dimension", dim_default))
        return {"provider": "onnx", "model": model, "dim": dim}
    # openai
    model = _prompt("OpenAI model", "text-embedding-3-small")
    dim_default = {"text-embedding-3-small": 1536, "text-embedding-3-large": 3072}.get(model, 1536)
    dim = int(_prompt("Dimension", str(dim_default)))
    print(
        "  (OpenAI API key is read from $OPENAI_API_KEY at request time — "
        "set it in the server env, not here.)"
    )
    return {"provider": "openai", "model": model, "dim": dim}


def _wizard_vlm(current_summary: SummaryConfig, current_vlm: VlmConfig) -> dict[str, Any]:
    _section(
        "Image summary / VLM",
        "When ON, the server generates a textual description for each image\n"
        "object (uses an LLM, needs an API key, costs $$). OFF by default —\n"
        "image objects are listed but not embedded.",
    )
    enabled = _prompt_bool("Enable image summary?", current_summary.enabled)
    if not enabled:
        return {"summary_enabled": False}
    provider = _prompt_choice("VLM provider", ["openai"], current_vlm.provider or "openai")
    model = _prompt("VLM model", current_vlm.model or "gpt-4o-mini")
    return {
        "summary_enabled": True,
        "summary_include_image_desc": True,
        "vlm_provider": provider,
        "vlm_model": model,
    }


def _wizard_milvus(current: MilvusConfig, env_resolved: bool) -> dict[str, Any]:
    _section(
        "Milvus (vector DB)",
        "Default = Milvus Lite (a file under $MFS_HOME). Switch to a remote\n"
        "Milvus / Zilliz Cloud by supplying the URI.",
    )
    # If current.uri comes from env (MFS_MILVUS_URI / ZILLIZ_URI), don't echo
    # the value back as a prompt default — the user picked env-as-source-of-truth
    # and we shouldn't surreptitiously copy it into the on-disk toml. Only
    # values that were already in server.toml itself become defaults.
    default_backend = "remote" if (current.uri.startswith("http") and not env_resolved) else "lite"
    backend = _prompt_choice("Backend", ["lite", "remote"], default_backend)
    if backend == "lite":
        return {"uri": "", "token": ""}
    uri_default = "" if env_resolved else current.uri
    tok_default = "" if env_resolved else current.token
    uri = _prompt("URI (e.g. https://xxx.zillizcloud.com)", uri_default)
    token = _prompt("Token", tok_default, secret=True)
    if env_resolved and not uri:
        print("  (kept empty — server will fall back to $MFS_MILVUS_URI / $ZILLIZ_URI at runtime.)")
    return {"uri": uri, "token": token}


def _wizard_metadata(current: MetadataConfig) -> dict[str, Any]:
    _section(
        "Metadata DB",
        "Default = SQLite (a file under $MFS_HOME). Switch to Postgres for\n"
        "team / multi-replica deployments.",
    )
    backend = _prompt_choice("Backend", ["sqlite", "postgres"], current.backend)
    if backend == "sqlite":
        return {"backend": "sqlite", "dsn": ""}
    dsn = _prompt("DSN (postgresql://user:pass@host/db)", current.dsn, secret=True)
    return {"backend": "postgres", "dsn": dsn}


def _wizard_object_store(current: ObjectStoreConfig) -> dict[str, Any]:
    _section(
        "Object store",
        "Holds transformation-cache artifacts (PDF→markdown, VLM descriptions).\n"
        "Default = local filesystem under $MFS_HOME. Switch to S3 (or MinIO /\n"
        "R2 / GCS via endpoint_url) for shared storage across server replicas.",
    )
    backend = _prompt_choice("Backend", ["local", "s3"], current.backend)
    if backend == "local":
        return {"backend": "local"}
    bucket = _prompt("Bucket", current.bucket)
    endpoint_url = _prompt("Endpoint URL (empty for AWS)", current.endpoint_url)
    region = _prompt("Region", current.region or "us-east-1")
    access_key = _prompt("Access key id", current.access_key_id, secret=True)
    secret_key = _prompt("Secret access key", current.secret_access_key, secret=True)
    return {
        "backend": "s3",
        "bucket": bucket,
        "endpoint_url": endpoint_url,
        "region": region,
        "access_key_id": access_key,
        "secret_access_key": secret_key,
    }


def _wizard_auth(current_token: str) -> dict[str, Any]:
    _section(
        "API authentication",
        "Clients authenticate via Bearer token. Press Enter to keep the\n"
        "current / auto-generated value; type a token to override; type '-'\n"
        "to disable auth (loopback-only deployments only).",
    )
    default = current_token or "(auto-generate)"
    val = _prompt("Token", default)
    if val == default or val == "(auto-generate)":
        return {"auto": True}
    if val == "-":
        return {"token": "-"}  # explicit opt-out (see _ensure_auth_token)
    return {"token": val}


# ─── runner ─────────────────────────────────────────────────────────────────


def _build_runners(milvus_from_env: bool) -> dict[str, Callable[[ServerConfig], dict[str, Any]]]:
    return {
        "embedding": lambda c: _wizard_embedding(c.embedding),
        "vlm": lambda c: _wizard_vlm(c.summary, c.vlm),
        "milvus": lambda c: _wizard_milvus(c.milvus, env_resolved=milvus_from_env),
        "metadata": lambda c: _wizard_metadata(c.metadata),
        "object_store": lambda c: _wizard_object_store(c.object_store),
        "auth": lambda c: _wizard_auth(c.auth_token),
    }


def _apply(section: str, current: dict[str, Any], answers: dict[str, Any]) -> dict[str, Any]:
    """Merge wizard answers into the running TOML dict (one section at a time)."""
    if section == "embedding":
        current["embedding"] = {
            "provider": answers["provider"],
            "model": answers["model"],
            "dim": answers["dim"],
        }
    elif section == "vlm":
        # `vlm` section toggles two top-level config sections: summary.enabled
        # (the master switch) and the vlm.* block (the model to use).
        summ = current.setdefault("summary", {})
        summ["enabled"] = answers["summary_enabled"]
        if answers["summary_enabled"]:
            summ["include_image_desc"] = answers.get("summary_include_image_desc", True)
            current["vlm"] = {"provider": answers["vlm_provider"], "model": answers["vlm_model"]}
    elif section == "milvus":
        current["milvus"] = {k: v for k, v in answers.items() if v}
    elif section == "metadata":
        current["metadata"] = {k: v for k, v in answers.items() if v}
    elif section == "object_store":
        current["object_store"] = {k: v for k, v in answers.items() if v != ""}
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
    if not _HAVE_TOMLI_W:
        # Fallback hand-roll: tomli_w is preferred but not in core deps yet.
        # The hand-rolled output covers the subset we emit (flat string /
        # int / bool values + a few nested tables) and round-trips through
        # tomllib without surprises.
        path.write_text(_render_toml(data))
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        tomli_w.dump(data, f)


def _render_toml(data: dict[str, Any], _parent: str = "") -> str:
    lines: list[str] = []
    # top-level scalars first
    for k, v in data.items():
        if isinstance(v, dict):
            continue
        lines.append(f"{k} = {_format_scalar(v)}")
    # then tables
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
    # strings (default) — escape doublequotes
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def _bootstrap_auth_token(token_dir: Path) -> str:
    """Mint a fresh API token + write to <token_dir>/server.token. Keeps
    the token next to whichever server.toml the wizard wrote, so the same
    invocation later picks it up via the resolved config path."""
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

    # Resolve where we'll write. Explicit --config wins; else $MFS_HOME/server.toml.
    out_path = Path(config_path) if config_path else (mfs_home() / "server.toml")
    if not config_path:
        os.environ.setdefault("MFS_HOME", str(mfs_home()))

    # Load existing toml (so partial wizard runs preserve untouched sections)
    # and existing resolved config (for sensible 'current' defaults).
    existing = _load_existing_toml(out_path)
    current_resolved = load_server_config(str(out_path) if out_path.exists() else None)
    # Detect "Milvus came from env, not from a prior toml" so we don't
    # silently surface env values as wizard defaults (which would then get
    # written to disk).
    milvus_from_env = bool(
        (os.environ.get("MFS_MILVUS_URI") or os.environ.get("ZILLIZ_URI"))
        and not existing.get("milvus", {}).get("uri")
    )
    runners = _build_runners(milvus_from_env)

    print("MFS server setup")
    print(f"  Writing to: {out_path}")
    print(f"  Sections:   {', '.join(target_sections)}")
    print("  Press Enter to accept the [default]. Ctrl-C aborts without saving.")

    try:
        for sect in target_sections:
            answers = runners[sect](current_resolved)
            existing = _apply(sect, existing, answers)
    except KeyboardInterrupt:
        print("\naborted; nothing written.")
        return 130

    # Backup, then write.
    if out_path.exists():
        bak = out_path.with_suffix(out_path.suffix + ".bak")
        bak.write_bytes(out_path.read_bytes())
        print(f"\nbacked up previous config to {bak}")
    _write_toml(out_path, existing)
    print(f"wrote {out_path}")

    # Auth: if the user picked auto-generate (or this is a fresh setup with no
    # explicit auth_token), mint a token so the first `mfs-server run` finds
    # it. Token lives next to server.toml so a --config /some/path setup and
    # the subsequent --config /some/path run see the same file.
    if "auth_token" not in existing:
        tok = _bootstrap_auth_token(out_path.parent)
        print(
            f"\nAPI token written to {out_path.parent / 'server.token'}\n"
            f"  Use this with clients on other machines:\n"
            f"    export MFS_API_TOKEN={tok}"
        )

    print("\nDone. Start the server with:")
    print("  mfs-server run")
    return 0


def main_entry(argv: list[str]) -> int:
    """argparse-friendly entry. argv is the args AFTER `setup`."""
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
