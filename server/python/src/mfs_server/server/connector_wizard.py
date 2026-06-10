"""Interactive per-connector add wizard — `mfs-server connector add <uri>`.

Distinct from `mfs-server setup` (which writes the server-level base config).
This wizard handles a single connector at a time: parse the scheme, walk the
fields declared in connector_schemas.SCHEMAS, write the resulting TOML to
$MFS_HOME/connectors/<alias>.toml, and (if a server is running locally) POST
to /v1/add so indexing kicks off immediately.

UI is built on `wizard_ui` (rich panels + questionary prompts), shared with
`mfs-server setup`. The TOML schema is unchanged from the pre-styling version.

Design choices:

- One connector per invocation. The user picks the scheme by passing a URI
  (`mfs-server connector add postgres://prod-db`), not by walking a menu of
  20 schemes.
- Fields are declarative in connector_schemas. Add a new connector → add its
  row there, the wizard picks it up automatically.
- Secrets stay on disk in the connector TOML; the wizard never echoes them
  back. The TOML lives next to the server (server-side, not pushed upstream).
- HTTP registration is best-effort: if `/v1/server/info` doesn't respond, the
  TOML is still written and the user is told to start the server.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..config import mfs_home
from . import wizard_ui as ui
from .connector_schemas import ConnectorField, ConnectorSchema, lookup_schema, supported_schemes


# ─── per-field prompt ────────────────────────────────────────────────────────


def _validate_secret_ref(s: str) -> str | None:
    """Inline check for `env:VAR` / `file:/path` indirections in secret prompts.

    The engine's `_resolve_ref` (see engine.py) translates these on plugin
    init; catching typos here turns "server boot fails with ValueError" into
    a friendly wizard re-prompt.
    """
    if s.startswith("env:"):
        name = s[4:].strip()
        if not name:
            return "env: needs a variable name, e.g. env:SLACK_BOT_TOKEN"
        import os as _os

        if name not in _os.environ:
            return f"env var {name!r} is not set in this shell — export it and retry"
        return None
    if s.startswith("file:"):
        from pathlib import Path

        path = Path(s[5:].strip())
        if not path.is_file():
            return f"file {path!s} does not exist or is not a regular file"
        return None
    return None


def _prompt_field(f: ConnectorField) -> Any:
    """Drive one ConnectorField through wizard_ui, with type coercion +
    inline validation (required, int parsing) handled by wizard_ui itself."""
    hint = f.help or None
    # Secret fields universally accept `env:VAR` / `file:/path` indirections
    # (resolved server-side by engine._resolve_ref). Surface this in the
    # prompt hint so users don't bake plaintext into the toml unless they
    # really mean to.
    if f.secret:
        hint = (hint + "  •  " if hint else "") + "or env:VAR / file:/path"

    def _int_validate(s: str) -> str | None:
        if f.multi:
            for piece in s.split(","):
                p = piece.strip()
                if not p:
                    continue
                try:
                    int(p)
                except ValueError:
                    return f"comma-separated integers expected; got {p!r}"
            return None
        try:
            int(s)
        except ValueError:
            return f"please enter a whole number (got {s!r})"
        return None

    if f.type == "bool":
        return ui.confirm(f.label, default=(f.default.lower() in ("y", "yes", "true", "1", "on")))

    if f.type == "int" and not f.multi:
        return ui.int_text(
            f.label,
            default=int(f.default) if f.default else 0,
            required=f.required,
        )

    if f.type == "int":
        validate = _int_validate
    elif f.secret:
        validate = _validate_secret_ref
    else:
        validate = None
    raw = ui.text(
        f.label,
        default=f.default,
        required=f.required,
        secret=f.secret,
        hint=hint,
        validate=validate,
    )
    if not raw:
        return None  # optional field, blank → omit from toml
    if f.multi:
        items = [s.strip() for s in raw.split(",") if s.strip()]
        if f.type == "int":
            return [int(x) for x in items]
        return items
    if f.type == "int":
        return int(raw)
    return raw


# ─── alias derivation ──────────────────────────────────────────────────────


def _derive_alias(uri: str) -> str:
    """`postgres://prod-db` -> 'prod-db', `slack://acme` -> 'acme', etc.

    For URIs without a clear host slug we fall back to the scheme — the user
    can rename the resulting TOML by hand if they care.
    """
    try:
        parsed = urlparse(uri)
        candidate = (parsed.netloc or parsed.path or "").strip("/")
        candidate = candidate.replace("/", "-").replace(":", "-")
        return candidate or parsed.scheme
    except Exception:  # noqa: BLE001
        return uri.split("://", 1)[0]


# ─── existing-connector listing (shared with setup wizard step 7) ──────────


def list_existing_connectors(connectors_dir: Path | None = None) -> list[dict[str, str]]:
    """Walk $MFS_HOME/connectors/*.toml and return [{alias, uri, scheme, path}, ...].

    The URI is read from the `# URI: <uri>` header line that `_render_toml`
    writes for every generated connector toml. If a hand-edited toml has no
    such header we fall back to `<scheme-from-filename>://<alias>` which
    matches what `mfs-server connector add` would have written.
    """
    try:
        import tomllib  # py3.11+
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

    cdir = connectors_dir or (mfs_home() / "connectors")
    rows: list[dict[str, str]] = []
    if not cdir.is_dir():
        return rows

    for path in sorted(cdir.glob("*.toml")):
        alias = path.stem
        uri = ""
        scheme = ""
        # Header: first line containing `# URI: <uri>` written by _render_toml.
        try:
            for line in path.read_text().splitlines()[:20]:
                stripped = line.strip()
                if stripped.startswith("# URI:"):
                    uri = stripped.split("URI:", 1)[1].strip()
                    break
                if stripped.startswith("# mfs-server connector config —"):
                    scheme = stripped.split("—", 1)[1].strip()
        except OSError:
            continue
        if not uri:
            # Hand-edited toml without a header — best effort.
            try:
                with open(path, "rb") as f:
                    data = tomllib.load(f)
                # Some scheme-specific tomls store the connection URI under
                # different keys; check the common ones.
                for k in ("uri", "url", "dsn"):
                    if isinstance(data.get(k), str) and "://" in data[k]:
                        uri = data[k]
                        break
            except Exception:  # noqa: BLE001
                pass
        if uri:
            scheme = scheme or uri.split("://", 1)[0]
        rows.append(
            {
                "alias": alias,
                "uri": uri or f"(no URI header in {path.name})",
                "scheme": scheme,
                "path": str(path),
            }
        )
    return rows


def _list_same_scheme(scheme: str) -> list[dict[str, str]]:
    """Filtered list_existing_connectors() — instances of the same scheme."""
    return [r for r in list_existing_connectors() if r["scheme"] == scheme]


def prompt_and_add_one() -> bool:
    """Interactive scheme + alias picker → delegate to run_connector_add.

    Used by the setup-wizard step 7 loop. Returns True if a connector was
    successfully registered (or its toml written), False if the user aborted
    the sub-wizard.
    """
    schemes_with_hints = [
        ("file", "local directory or remote upload — usually `mfs add ./path` instead"),
        ("postgres", "PostgreSQL — DSN-based"),
        ("mysql", "MySQL / MariaDB — DSN-based"),
        ("mongo", "MongoDB — URI-based"),
        ("snowflake", "Snowflake warehouse"),
        ("bigquery", "Google BigQuery"),
        ("slack", "Slack workspace — needs bot token"),
        ("notion", "Notion workspace"),
        ("github", "GitHub repository"),
        ("web", "Web site (crawled)"),
        ("s3", "S3 bucket"),
    ]
    # Only offer schemes we actually have a wizard schema for.
    available = set(supported_schemes())
    choices = [(s, h) for s, h in schemes_with_hints if s in available]
    if not choices:
        ui.warn("no connector schemes are registered; cannot add interactively.")
        return False
    scheme = ui.select("Connector type", choices, default=choices[0][0])

    if scheme == "file":
        ui.info("Run `mfs add <path>` directly for file connectors — skipping.")
        return False

    existing = _list_same_scheme(scheme)
    if existing:
        ui.note(f"Existing {scheme}:// instances:")
        for row in existing:
            ui.note(f"  • {row['alias']}  ({row['uri']})")

    alias = ui.text(
        "Instance alias",
        required=True,
        hint=f"becomes the URI host: {scheme}://<alias>",
        validate=lambda v: (
            None
            if v.replace("-", "").replace("_", "").isalnum()
            else "alias must be alphanumeric (- and _ allowed)"
        ),
    )
    uri = f"{scheme}://{alias}"
    # Reuse the full single-connector flow.
    rc = run_connector_add(uri)
    return rc == 0


# ─── TOML rendering (small subset — shared style with setup_wizard) ────────


def _format_scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_format_scalar(x) for x in v) + "]"
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def _render_toml(
    scheme: str,
    uri: str,
    fields: dict[str, Any],
    extras_hint: str,
    *,
    objects: list[dict[str, Any]] | None = None,
) -> str:
    lines = [
        f"# mfs-server connector config — {scheme}",
        f"# URI: {uri}",
        "#",
        "# Generated by `mfs-server connector add`. You can hand-edit this file and",
        "# re-run `mfs-server connector add <uri>` (or `mfs add <uri> --update`) to",
        "# apply changes; secrets are stored in plaintext here, so chmod 0600 the",
        "# enclosing directory.",
    ]
    if extras_hint:
        lines.append("#")
        for ln in extras_hint.strip().splitlines():
            lines.append(f"# {ln}")
    lines.append("")
    for k, v in fields.items():
        lines.append(f"{k} = {_format_scalar(v)}")
    for obj in objects or []:
        lines.append("")
        lines.append("[[objects]]")
        for k, v in obj.items():
            lines.append(f"{k} = {_format_scalar(v)}")
    lines.append("")
    return "\n".join(lines)


# ─── introspection + per-table prompt ──────────────────────────────────────


class _IntrospectError(Exception):
    """Wraps non-KeyboardInterrupt failures (auth, network, missing tables, ...)
    from connect() / introspect_for_wizard() so the runner can keep going with
    no [[objects]] blocks rather than abort."""


class _StubStateStore:
    """No-op StateStore for the wizard. introspect_for_wizard() doesn't touch
    state, but ConnectorPlugin.__init__ requires one."""

    async def get(self, key: str) -> Any:
        return None

    async def set(self, key: str, value: Any) -> None:
        return None

    async def delete(self, key: str) -> None:
        return None

    async def checkpoint(self) -> None:
        return None


def _introspect_and_prompt(scheme: str, uri: str, values: dict[str, Any]) -> list[dict[str, Any]]:
    """Connect with the just-collected creds, walk tables / collections, prompt
    per-object for text_fields / locator_fields. Returns ready-to-render
    [[objects]] dicts. Returns [] if the scheme doesn't implement introspect.
    """
    import asyncio
    import contextlib

    from ..connectors.base import ConnectorContext
    from ..connectors.registry import get_plugin_cls, load_builtin

    load_builtin()
    plugin_cls = get_plugin_cls(scheme)
    if plugin_cls is None:
        return []

    state = _StubStateStore()
    ctx = ConnectorContext(state, connector_id="wizard", namespace_id="default")
    try:
        plugin = plugin_cls(config=dict(values), credential=None, ctx=ctx)
    except Exception as e:  # noqa: BLE001
        raise _IntrospectError(f"plugin instantiation failed: {e}") from e

    async def go() -> dict[str, dict]:
        try:
            await plugin.connect()
        except Exception as e:  # noqa: BLE001
            raise _IntrospectError(f"connect failed (check credentials?): {e}") from e
        try:
            return await plugin.introspect_for_wizard()
        finally:
            with contextlib.suppress(Exception):
                await plugin.close()

    ui.section("Live introspection", f"Connecting to {uri} and listing tables / collections …")
    try:
        introspect = asyncio.run(go())
    except _IntrospectError:
        raise
    except Exception as e:  # noqa: BLE001
        raise _IntrospectError(str(e)) from e

    if not introspect:
        ui.info(
            "no tables / collections to configure — the connector doesn't support "
            "introspection or the source is empty."
        )
        return []

    ui.info(
        f"found {len(introspect)} object(s). For each, you can accept the auto-detected "
        "text / PK fields, edit them, or skip the object entirely."
    )
    return _prompt_per_object(introspect)


def _prompt_per_object(introspect: dict[str, dict]) -> list[dict[str, Any]]:
    """Walk the introspect output, prompt for use / edit / skip per object."""
    objects: list[dict[str, Any]] = []
    for path, info in introspect.items():
        cols = info.get("columns", [])
        pk = info.get("pk", [])
        text_default = info.get("text_candidates", [])
        if not text_default and not cols:
            ui.note(f"\n  {path}: empty schema → skipping")
            continue
        col_summary = ", ".join(
            f"{c['name']}({c['type']})" + ("*" if c.get("pk") else "") for c in cols[:8]
        )
        if len(cols) > 8:
            col_summary += f", …(+{len(cols) - 8} more)"

        ui.console.print()
        ui.console.print(f"  [bold]{path}[/bold]")
        ui.list_kv(
            [
                ("columns       ", col_summary or "(none discovered)"),
                (
                    "text default  ",
                    str(text_default) if text_default else "(none — table has no string cols)",
                ),
                ("PK default    ", str(pk) if pk else "(none — set locator_fields manually)"),
            ],
            indent="    ",
        )
        choice = ui.select(
            "index this object?",
            [
                ("use", "use the auto-detected fields above"),
                ("edit", "type custom text_fields / locator_fields"),
                ("skip", "don't index this object"),
            ],
            default="use" if text_default else "skip",
        )
        if choice == "skip":
            continue
        if choice == "use":
            text_fields = list(text_default)
            locator_fields = list(pk)
        else:
            raw_t = ui.text(
                "text_fields (comma-separated)", default=",".join(text_default), required=True
            )
            raw_l = ui.text("locator_fields (comma-separated)", default=",".join(pk))
            text_fields = [s.strip() for s in raw_t.split(",") if s.strip()]
            locator_fields = [s.strip() for s in raw_l.split(",") if s.strip()]
        if not text_fields:
            ui.warn(f"{path}: text_fields empty → skipping (would index zero content)")
            continue
        block: dict[str, Any] = {"match": path, "text_fields": text_fields}
        if locator_fields:
            block["locator_fields"] = locator_fields
        objects.append(block)
    return objects


# ─── HTTP registration via local /v1/add ────────────────────────────────────


def _post_add(uri: str, config_toml: str, server_port: int = 13619) -> tuple[bool, str]:
    """Best-effort POST /v1/add against the local server."""
    import urllib.error
    import urllib.request

    token = os.environ.get("MFS_API_TOKEN")
    if not token:
        token_file = mfs_home() / "server.token"
        if token_file.exists():
            token = token_file.read_text().strip()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = json.dumps({"target": uri, "config": config_toml}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{server_port}/v1/add",
        data=payload,
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode()
            try:
                parsed = json.loads(body)
                return True, f"job_id={parsed.get('job_id')}"
            except json.JSONDecodeError:
                return True, body
    except urllib.error.HTTPError as e:
        return False, f"server returned {e.code}: {e.read().decode()[:200]}"
    except (urllib.error.URLError, ConnectionRefusedError, TimeoutError) as e:
        return False, f"server unreachable ({e})"


# ─── feishu user-OAuth (inline device flow) ─────────────────────────────────


def _resolve_secret_ref(v: Any) -> str:
    """Resolve an `env:VAR` / `file:/path` ref to its value (for the device flow, which
    needs the real app_secret). A plain value passes through unchanged."""
    if isinstance(v, str):
        if v.startswith("env:"):
            return os.environ.get(v[4:], "")
        if v.startswith("file:"):
            try:
                return Path(v[5:]).read_text().strip()
            except OSError:
                return ""
    return v or ""


def run_feishu_device_login(values: dict[str, Any], alias: str) -> bool:
    """Run the Feishu OAuth device flow for a feishu user-mode connector and backfill
    `oauth_state_file` into `values`. Returns True on success / not-applicable."""
    if values.get("auth", "user") != "user":
        return True  # tenant mode needs no user authorization
    from ..connectors.feishu.auth_login import perform_device_login

    app_id = values.get("app_id")
    app_secret = _resolve_secret_ref(values.get("app_secret"))
    if not (app_id and app_secret):
        ui.warn("feishu user mode needs app_id + app_secret to authorize.")
        return False
    region = values.get("region", "feishu")
    state_path = values.get("oauth_state_file") or str(mfs_home() / f"feishu-{alias}.oauth.json")
    values["oauth_state_file"] = state_path
    ui.note("Authorize this app to read your Feishu chats + docs:")
    return perform_device_login(
        app_id, app_secret, state_path, region=region, info=ui.note, prompt=ui.emphasis
    )


# ─── runner ─────────────────────────────────────────────────────────────────


def run_connector_add(
    uri: str,
    *,
    no_sync: bool = False,
    out_dir: Path | None = None,
    server_port: int = 13619,
) -> int:
    parsed = urlparse(uri)
    scheme = parsed.scheme
    if not scheme:
        print(f"error: target '{uri}' is missing a scheme", file=sys.stderr)
        print(f"       supported: {', '.join(supported_schemes())}", file=sys.stderr)
        return 2

    if scheme == "file":
        print(
            "file:// connectors don't need a wizard — just `mfs add ./your/path`.\n"
            "If you want a custom alias: `mfs add file://my-alias /abs/path`.",
            file=sys.stderr,
        )
        return 2

    schema: ConnectorSchema | None = lookup_schema(scheme)
    if not schema:
        print(f"error: no interactive schema for scheme '{scheme}'", file=sys.stderr)
        print(f"       supported: {', '.join(supported_schemes())}", file=sys.stderr)
        print(
            "       (use `mfs add <uri> --config /path/to/config.toml` directly "
            "for unsupported schemes)",
            file=sys.stderr,
        )
        return 2

    out_dir = out_dir or (mfs_home() / "connectors")
    out_dir.mkdir(parents=True, exist_ok=True)
    alias = _derive_alias(uri)
    out_path = out_dir / f"{alias}.toml"

    ui.clear()
    ui.banner(
        f"mfs-server connector add: {scheme}",
        lines=[
            schema.summary,
            f"URI:        {uri}",
            f"Writing to: {out_path}"
            + ("  (existing file will be backed up to .bak)" if out_path.exists() else ""),
            "Press Ctrl-C to abort without saving",
        ],
    )
    # Help users avoid alias collisions — list any same-scheme instances they
    # already registered. The wizard will still proceed (and back up .bak)
    # even if alias matches; this just makes "I forgot what I had" visible.
    siblings = [r for r in _list_same_scheme(scheme) if r["alias"] != alias]
    if siblings:
        ui.note(f"Other {scheme}:// instances on this host:")
        for row in siblings:
            ui.note(f"  • {row['alias']}  ({row['uri']})")
    if out_path.exists():
        ui.warn(f"alias '{alias}' already exists — re-running will overwrite the previous TOML.")

    total = 1 + (1 if scheme in {"postgres", "mysql", "mongo", "snowflake", "bigquery"} else 0)
    ui.section("Credentials", "", step=1, total=total)

    try:
        values: dict[str, Any] = {}
        for f in schema.fields:
            v = _prompt_field(f)
            if v is not None:
                values[f.name] = v
    except KeyboardInterrupt:
        ui.warn("aborted; nothing written.")
        return 130

    objects: list[dict[str, Any]] = []
    try:
        if total > 1:
            ui.section("Per-object configuration", "", step=2, total=total)
        objects = _introspect_and_prompt(scheme, uri, values)
    except KeyboardInterrupt:
        ui.warn("aborted; nothing written.")
        return 130
    except _IntrospectError as e:
        ui.warn(f"could not introspect schema: {e}")
        ui.note(
            "writing the connector TOML without [[objects]] blocks; you can\n"
            "  add them by hand later — see the connector reference docs."
        )

    # feishu user mode: authorize inline (browser device flow) so the connector works
    # right after add, and record the resulting oauth_state_file in the TOML.
    if scheme == "feishu":
        try:
            if not run_feishu_device_login(values, alias):
                ui.warn("authorization not completed; nothing written.")
                return 130
        except KeyboardInterrupt:
            ui.warn("aborted; nothing written.")
            return 130

    rendered = _render_toml(scheme, uri, values, schema.extras_hint, objects=objects)

    if out_path.exists():
        bak = out_path.with_suffix(out_path.suffix + ".bak")
        bak.write_bytes(out_path.read_bytes())
        ui.note(f"backed up previous config to {bak}")
    out_path.write_text(rendered)
    try:
        out_path.chmod(0o600)
    except OSError:
        pass
    ui.emphasis(f"wrote {out_path}")

    if no_sync:
        ui.note(
            f"\n--no-sync set; skipping server registration.\n"
            f"  To register later: `mfs add {uri} --config {out_path}`."
        )
        return 0

    ui.note("\nregistering against the local server …")
    ok, msg = _post_add(uri, rendered, server_port=server_port)
    if ok:
        ui.emphasis(f"  ok: {msg}")
        ui.note(f"\n  Watch progress: `mfs status {uri}`")
        ui.note("  List jobs:      `mfs job ls`")
        return 0

    ui.warn(f"could not register against local server: {msg}")
    ui.note(
        f"\n  TOML is saved. To register later (after starting the server):\n"
        f"    mfs add {uri} --config {out_path}"
    )
    return 0


def main_entry(argv: list[str]) -> int:
    import argparse

    p = argparse.ArgumentParser(
        prog="mfs-server connector add",
        description="Interactive wizard for adding a connector.",
    )
    p.add_argument("uri", help=f"Connector URI (one of: {', '.join(supported_schemes())})")
    p.add_argument(
        "--no-sync",
        action="store_true",
        help="Write the TOML but don't POST /v1/add (skip immediate registration)",
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Where to write the connector TOML (default: $MFS_HOME/connectors/)",
    )
    p.add_argument(
        "--server-port",
        type=int,
        default=13619,
        help="Port the local mfs-server listens on (default: 13619)",
    )
    args = p.parse_args(argv)
    return run_connector_add(
        args.uri,
        no_sync=args.no_sync,
        out_dir=args.out_dir,
        server_port=args.server_port,
    )


def auth_entry(argv: list[str]) -> int:
    """`mfs-server connector auth <uri>` — (re)authorize a connector's user OAuth.

    Reads the connector's saved TOML for app credentials + region + oauth_state_file and
    runs the browser device flow, refreshing the stored token. Use after first add if you
    skipped authorization, or when the authorization has expired / been revoked."""
    import argparse

    p = argparse.ArgumentParser(
        prog="mfs-server connector auth",
        description="Authorize or re-authorize a connector's user OAuth (browser device flow).",
    )
    p.add_argument("uri", help="Connector URI, e.g. feishu://my-workspace")
    args = p.parse_args(argv)

    scheme = urlparse(args.uri).scheme
    if scheme != "feishu":
        print(f"connector auth currently supports feishu only (got '{scheme}')", file=sys.stderr)
        return 2

    alias = _derive_alias(args.uri)
    toml_path = mfs_home() / "connectors" / f"{alias}.toml"
    if not toml_path.exists():
        print(
            f"no connector config at {toml_path}; run `mfs-server connector add {args.uri}` first",
            file=sys.stderr,
        )
        return 2
    try:
        import tomllib  # py3.11+
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]
    with open(toml_path, "rb") as f:
        data = tomllib.load(f)

    if data.get("auth", "user") != "user":
        print(f"{toml_path} uses auth='tenant' (app-only); no user authorization needed.")
        return 0
    app_id = data.get("app_id")
    app_secret = _resolve_secret_ref(data.get("app_secret"))
    if not (app_id and app_secret):
        print(f"{toml_path} is missing app_id / app_secret", file=sys.stderr)
        return 2
    region = data.get("region", "feishu")
    state_path = data.get("oauth_state_file") or str(mfs_home() / f"feishu-{alias}.oauth.json")

    from ..connectors.feishu.auth_login import perform_device_login

    ok = perform_device_login(app_id, app_secret, state_path, region=region)
    if ok:
        print(f"\nauthorized — {args.uri} is ready. Run `mfs sync {args.uri}` to index.")
        return 0
    return 1


def list_entry(argv: list[str]) -> int:
    """`mfs-server connector list` — print every connector TOML under
    $MFS_HOME/connectors/ along with its URI and scheme.

    This is the "where did I leave my connectors" command: every successful
    `connector add` writes a toml here, so this is the local source of truth
    for what the operator has configured (the running server's view via
    /v1/status may differ if the server is down or hasn't picked up an
    edit).
    """
    import argparse

    p = argparse.ArgumentParser(
        prog="mfs-server connector list",
        description="List connector TOMLs under $MFS_HOME/connectors/.",
    )
    p.add_argument(
        "--connectors-dir",
        type=Path,
        default=None,
        help="Override the connectors directory (default: $MFS_HOME/connectors/)",
    )
    args = p.parse_args(argv)

    rows = list_existing_connectors(args.connectors_dir)
    cdir = args.connectors_dir or (mfs_home() / "connectors")
    if not rows:
        print(f"No connectors registered under {cdir}.")
        print("Add one with: mfs-server connector add <scheme>://<alias>")
        return 0
    print(f"{len(rows)} connector(s) under {cdir}:\n")
    width = max(len(r["alias"]) for r in rows)
    for r in rows:
        print(f"  {r['alias']:<{width}}  {r['uri']}")
    print()
    print(
        "Each TOML is the on-disk spec for one instance. The running server "
        "registers them via POST /v1/add (`mfs add <uri> --update` after edits)."
    )
    return 0
