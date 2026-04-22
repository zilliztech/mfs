"""`mfs config` subcommand group.

Read/write the user's ~/.mfs/config.toml. Designed to be safe for non-tty
automation: ``set`` rewrites the file via ``tomli_w`` (losing user comments
on edited files — regenerate with ``mfs config init --force`` to get them
back). ``show`` annotates each value with where it came from
([default], [config.toml], [env]).
"""

from __future__ import annotations

import json as _json
from dataclasses import asdict

import click

from .config import (
    Config,
    coerce_value,
    config_path,
    default_config_template,
    env_source_for,
    file_overrides,
    get_value,
    known_keys,
    load_config,
    write_config,
)
from .output.display import error


SECRET_KEYS: frozenset[str] = frozenset({"embedding.api_key", "llm.api_key", "milvus.token"})


@click.group(name="config", help="Manage ~/.mfs/config.toml.")
def config_group() -> None:
    pass


# ------------------------------------------------------------------ path


@config_group.command("path", help="Print the absolute path of config.toml.")
def cmd_path() -> None:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    click.echo(str(p))


# ------------------------------------------------------------------ init


@config_group.command("init", help="Generate a commented default config.toml.")
@click.option("--force", is_flag=True, help="Overwrite an existing config.toml.")
def cmd_init(force: bool) -> None:
    # Don't go through ensure_mfs_home() — that would auto-write the file
    # before we get a chance to honor the `--force` semantics.
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists() and not force:
        error(f"{p} already exists. Re-run with --force to overwrite.")
        raise click.exceptions.Exit(1)
    # Back up any existing config so --force isn't silently destructive.
    if p.exists() and force:
        import time as _time
        backup = p.with_name(f"{p.name}.bak.{int(_time.time())}")
        try:
            backup.write_bytes(p.read_bytes())
            click.echo(f"backed up existing config to {backup}")
        except OSError as exc:
            error(f"could not back up existing config: {exc}")
            raise click.exceptions.Exit(1) from exc
    p.write_text(default_config_template(), encoding="utf-8")
    click.echo(f"wrote {p}")


# ------------------------------------------------------------------ get


@config_group.command("get", help="Print one config value (e.g. embedding.provider).")
@click.argument("key", type=str)
def cmd_get(key: str) -> None:
    cfg = load_config()
    try:
        value = get_value(cfg, key)
    except KeyError:
        error(f"unknown config key: {key}")
        _print_known_keys()
        raise click.exceptions.Exit(2)
    if value is None:
        click.echo("")
        return
    if isinstance(value, list):
        click.echo(",".join(str(v) for v in value))
        return
    click.echo(str(value))


# ------------------------------------------------------------------ set


@config_group.command("set", help="Persist a config value to ~/.mfs/config.toml.")
@click.argument("key", type=str)
@click.argument("value", type=str)
def cmd_set(key: str, value: str) -> None:
    if key not in known_keys():
        error(f"unknown config key: {key}")
        _print_known_keys()
        raise click.exceptions.Exit(2)
    try:
        coerced = coerce_value(key, value)
    except (ValueError, TypeError) as exc:
        error(f"invalid value for {key}: {exc}")
        raise click.exceptions.Exit(2) from exc

    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = file_overrides() or {}
    section, fname = key.split(".", 1)
    section_data = dict(data.get(section, {}))
    section_data[fname] = coerced
    data[section] = section_data

    # When a user switches *.provider, auto-realign *.model to the new provider's
    # default — but only if the current model is unset or still the old
    # provider's default. A bespoke user model stays intact.
    note: str | None = None
    if key in ("embedding.provider", "llm.provider"):
        note = _auto_realign_model(section, coerced, data)

    p = write_config(data)
    click.echo(f"set {key} = {_format_value(coerced)} in {p}")
    if note:
        click.echo(note)


def _auto_realign_model(section: str, new_provider: str, data: dict) -> str | None:
    """If *section*.model is unset or a default of a known provider, replace it
    with ``DEFAULT_MODELS[new_provider]`` in *data* (mutates) and return a note."""
    from .embedder import DEFAULT_MODELS as EMB_DEFAULTS
    from .llm import DEFAULT_MODELS as LLM_DEFAULTS

    defaults = EMB_DEFAULTS if section == "embedding" else LLM_DEFAULTS
    new_default = defaults.get(new_provider)
    if not new_default:
        return None

    section_data = dict(data.get(section, {}))
    current = section_data.get("model", "")
    # Unset (empty) or matches any *other* known-provider default → auto-realign.
    is_default_like = (
        not current
        or current in set(defaults.values())
    )
    if not is_default_like or current == new_default:
        return None
    section_data["model"] = new_default
    data[section] = section_data
    return f'note: also set {section}.model = "{new_default}" (default for {new_provider})'


# ------------------------------------------------------------------ show


@config_group.command("show", help="Display all effective config values with their source.")
@click.option("--json", "output_json", is_flag=True, help="JSON output (no source annotations).")
def cmd_show(output_json: bool) -> None:
    cfg = load_config()
    if output_json:
        click.echo(_json.dumps(_config_to_dict(cfg), indent=2, ensure_ascii=False))
        return

    overrides = file_overrides()
    lines: list[str] = []
    current_section: str | None = None
    for key in known_keys():
        section, fname = key.split(".", 1)
        if section != current_section:
            if current_section is not None:
                lines.append("")
            lines.append(f"[{section}]")
            current_section = section
        value = get_value(cfg, key)
        source = _source_label(key, value, cfg, overrides)
        rendered = _redact(key, value)
        lines.append(f"  {fname:<22} = {rendered:<40} {source}")
    click.echo("\n".join(lines))


# ------------------------------------------------------------------ helpers


def _config_to_dict(cfg: Config) -> dict:
    out = {}
    for section in ("embedding", "llm", "indexing", "cache", "milvus"):
        out[section] = asdict(getattr(cfg, section))
    return out


def _source_label(key: str, value, cfg: Config, overrides: dict) -> str:
    env_name = env_source_for(key, cfg)
    if env_name and value:
        return f"[env {env_name}]"
    section, fname = key.split(".", 1)
    if section in overrides and fname in overrides[section]:
        return "[config.toml]"
    return "[default]"


def _redact(key: str, value) -> str:
    if value is None:
        return '""'
    if key in SECRET_KEYS and value:
        return "<set>"
    if isinstance(value, list):
        return _format_value(value)
    if isinstance(value, str):
        return f'"{value}"'
    return str(value)


def _format_value(value) -> str:
    if isinstance(value, list):
        inner = ",".join(str(v) for v in value)
        return f"[{inner}]"
    if isinstance(value, str):
        return f'"{value}"'
    return str(value)


def _print_known_keys() -> None:
    click.echo("known keys:", err=True)
    for k in known_keys():
        click.echo(f"  {k}", err=True)


__all__ = ["config_group"]
