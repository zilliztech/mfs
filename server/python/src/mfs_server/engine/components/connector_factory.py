"""``ConnectorFactory`` — target resolution / credential redact+resolve / plugin build.

Factory + Abstract Product: ``ConnectorPlugin`` (in ``connectors/base.py``) is the
abstract product; the factory never instantiates a concrete plugin class directly —
it delegates product selection to ``connectors/registry.get_plugin_cls(ctype)`` and
only owns the *construction protocol* (credential ref resolution, ``credential_ref``
pop, ``ConnectorContext`` assembly, ``FileConfig`` special-case, object_config
resolver injection).

Boundary: this module NEVER writes the ``connectors`` table. ``ObjectRepository``
calls ``factory.redact(config)`` to obtain a sanitized config before persisting; the
factory only ever produces in-memory plugin instances + sanitized config dicts.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from dataclasses import replace as _replace
from typing import Any

from pydantic import ValidationError as _PydanticValidationError

from ...config import ServerConfig
from ...connectors.base import (
    ConnectorConfigSchema,
    ConnectorContext,
    ConnectorPlugin,
    ObjectConfig,
    preset_object_config,
)
from ...connectors.registry import get_plugin_cls
from ...storage.file_state import FileStateStore
from ...storage.metadata import MetadataStore
from ..state import ConnectorStateStore

# --- product value objects (replace positional tuples) ---


@dataclass(frozen=True)
class TargetResolution:
    """Product of target resolution (replaces ``tuple[str, str, str, dict]``).

    ctype         — connector type, e.g. 'file' / 'github' / 'postgres'
    connector_uri — stable identity URI for this target
                    (file://local<abs> / github://owner/repo / ...)
    scheme        — resolved scheme (usually equal to ctype; kept separate to
                    preserve the old positional semantics)
    config        — default config derived from the target
                    ({root, client_id} / {repo} / {})
    """

    ctype: str
    connector_uri: str
    scheme: str
    config: dict


@dataclass(frozen=True)
class BuiltPlugin:
    """Product of plugin build (replaces ``tuple[plugin, ctx]``).

    plugin — instantiated plugin; credential_ref popped, ctx._resolver injected
    ctx    — the ConnectorContext bound to the plugin
    """

    plugin: ConnectorPlugin
    ctx: ConnectorContext


@dataclass(frozen=True)
class ResolvedConnector:
    """Product of open_path (read-path location + plugin rebuild).

    cid           — id of the registered connector
    connector_uri — its root_uri
    relpath       — path relative to the connector root (leading '/')
    built         — the rebuilt BuiltPlugin (already connected)
    """

    cid: str
    connector_uri: str
    relpath: str
    built: BuiltPlugin


def _match_object_config(objects_cfg: list, path: str) -> ObjectConfig | None:
    """Find the user ``[[objects]]`` entry whose ``match`` matches this path,
    first-match wins; None when nothing matches (caller falls back to a built-in
    preset). Verbatim migration of the former ``engine._match_object_config``."""
    import fnmatch

    fields = ObjectConfig.__dataclass_fields__
    for o in objects_cfg:
        m = o.get("match", "")
        if m and (fnmatch.fnmatch(path, m) or fnmatch.fnmatch(path.lstrip("/"), m) or m in path):
            return ObjectConfig(**{k: v for k, v in o.items() if k != "match" and k in fields})
    return None


def _summarize_validation_error(e: _PydanticValidationError) -> str:
    """Flatten pydantic's multi-line ValidationError into one clean,
    grep-able line naming every offending field — pydantic's own str(e) is
    verbose and spans several lines, awkward inside a single CLI error."""
    parts = []
    for err in e.errors():
        loc = ".".join(str(x) for x in err["loc"]) or "(top-level)"
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)


# scheme extractor for target URIs; a bare path (no scheme) defaults to file.
_SCHEME_RE = re.compile(r"^([a-z][a-z0-9+.\-]*)://")


# --- CredentialService: the single redact + resolve entry point ---
class CredentialService:
    """Single entry point for credential redact + resolve.

    - redact:  recursively strip inline secrets before persistence (keeps
               env:/file: references)
    - resolve: turn env:/file: references into real values before plugin build

    Security invariants (engine-redesign.md §5):
    1. Credentials are resolved ONLY through resolve(); business code never reads os.environ[...] directly.
    2. config_json is persisted ONLY after redact().
    3. resolve() raises explicitly on unimplemented schemes (secret:/vault:), never silently using them as literal tokens.
    4. redact() keeps env:/file: as-is; secret:/vault: under a secret key are redacted so the persisted copy leaves no unimplemented-scheme reference.
    """

    # substrings that mark a config key as holding a secret (case-insensitive, recursive).
    # dsn carries credentials but has no obvious word, so it's listed.
    _SECRET_SUBSTRINGS = (
        "token",
        "secret",
        "password",
        "passwd",
        "apikey",
        "api_key",
        "access_key",
        "private_key",
        "refresh",
        "credential",
        "dsn",
        "session_id",
    )
    # credential-reference schemes that are actually resolved (kept, not redacted).
    # secret:/vault: are unimplemented; under a secret key they get redacted, and resolve() raises on them.
    _CRED_REF_PREFIXES = ("env:", "file:")
    # a connection string carrying inline credentials: scheme://user:password@host…
    _CONN_URI_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://[^/\s:@]+:[^/\s@]+@")
    _REDACTED = "<redacted: use credential_ref=env:VAR>"

    # --- validate ---

    @classmethod
    def validate_no_plaintext_secrets(cls, value: Any, key_path: str = "") -> None:
        """Recursively reject inline plaintext secrets BEFORE redact()/persistence.
        A caller must supply credentials as env:VAR / file:/abs/path references; a
        raw value under a secret-looking key (or an inline user:pass@host
        connection string) is rejected outright rather than silently masked, so a
        rebuild can never end up resolving a redaction placeholder as if it were a
        real credential. Mirrors redact()'s detection surface exactly."""
        if isinstance(value, dict):
            for k, v in value.items():
                cls.validate_no_plaintext_secrets(v, f"{key_path}.{k}" if key_path else str(k))
            return
        if isinstance(value, list):
            for v in value:
                cls.validate_no_plaintext_secrets(v, key_path)
            return
        if not isinstance(value, str) or value in ("", None):
            return
        if value.startswith(cls._CRED_REF_PREFIXES):
            return  # a safe credential reference
        leaf_key = key_path.rsplit(".", 1)[-1] if key_path else ""
        if cls.is_secret_key(leaf_key) or cls._CONN_URI_RE.search(value):
            raise ValueError(
                f"plaintext secret in config field {key_path!r}: use "
                "credential_ref=env:VAR or file:/abs/path, not a literal value"
            )

    # --- redact ---

    @classmethod
    def is_secret_key(cls, key: str) -> bool:
        kl = str(key).lower()
        return any(s in kl for s in cls._SECRET_SUBSTRINGS)

    @classmethod
    def redact(cls, value: Any, key_is_secret: bool = False) -> Any:
        """Recursively redact raw inline secrets from a config before persistence.
        A credential_ref (env:/secret:/file:/vault:) is kept; anything else under a
        secret-looking key is replaced with None (never a placeholder string a
        plugin could mistake for a real value). Recurses into dicts/lists so nested
        OAuth token dicts don't leak. Verbatim migration of ``_redact_config``,
        except the placeholder is now None instead of a literal sentinel string —
        callers should have already rejected plaintext secrets via
        ``validate_no_plaintext_secrets``; this is defense-in-depth only."""
        if isinstance(value, dict):
            return {k: cls.redact(v, cls.is_secret_key(k)) for k, v in value.items()}
        if isinstance(value, list):
            return [cls.redact(v, key_is_secret) for v in value]
        if isinstance(value, str) and value.startswith(cls._CRED_REF_PREFIXES):
            return value  # a safe credential reference, keep as-is
        if key_is_secret and value not in (None, "", [], {}):
            return None
        # value-level catch: an inline connection string carrying a password leaks
        # via a field name (dsn/uri/url/connection) that doesn't look secret — redact by shape.
        if isinstance(value, str) and cls._CONN_URI_RE.search(value):
            return None
        return value

    # --- resolve ---

    @staticmethod
    def resolve(value: Any) -> Any:
        """Resolve a credential reference to its actual value: ``env:VAR`` ->
        environment, ``file:/path`` -> the file's contents (k8s/docker secret
        mounts). Non-ref values pass through unchanged. ``secret:``/``vault:``
        raise loudly so an advertised-but-unimplemented scheme can't masquerade as
        a working ref and silently fail auth. Verbatim migration of ``_resolve_ref``."""
        if not isinstance(value, str):
            return value
        if value == CredentialService._REDACTED:
            # defense-in-depth: a pre-fix row (or any other path that still produced
            # the old sentinel string) must never be resolved as a literal credential.
            raise ValueError(
                f"credential_ref {value!r}: this is a redaction placeholder, not a "
                "real credential — re-register the connector with credential_ref=env:VAR "
                "or file:/abs/path"
            )
        if value.startswith("env:"):
            name = value[4:]
            if name not in os.environ:
                raise ValueError(
                    f"credential_ref {value!r}: environment variable {name} is not set"
                )
            return os.environ[name]
        if value.startswith("file:"):
            try:
                with open(value[5:], encoding="utf-8") as f:
                    return f.read().strip()
            except OSError as e:
                raise ValueError(f"credential_ref {value!r}: cannot read secret file ({e})") from e
        if value.startswith(("secret:", "vault:")):
            raise ValueError(
                f"credential_ref scheme {value.split(':', 1)[0]!r} is not implemented "
                f"(use env: or file:)"
            )
        return value


# --- PluginBuilder: instantiation + ctx assembly ---
class PluginBuilder:
    """Instantiator of the Abstract Product: ctype + config + cid -> BuiltPlugin.

    Does NOT select the product class (that's ``registry.get_plugin_cls``); it only
    owns the construction protocol: credential resolution / credential_ref pop /
    ctx assembly / FileConfig special-case / object_config resolver injection.
    """

    def __init__(self, cfg: ServerConfig, meta: MetadataStore, creds: CredentialService):
        self._cfg = cfg
        self._meta = meta
        self._ns = cfg.namespace
        self._creds = creds

    def build(self, ctype: str, config: dict, connector_id: str) -> BuiltPlugin:
        cls = get_plugin_cls(ctype)
        if cls is None:
            raise NotImplementedError(f"no plugin for {ctype}")
        # Resolve credential references at build time so secrets live in the
        # environment, not in connectors.config_json. The stored config keeps the
        # env:VAR ref / _credential_ref; only this in-memory copy carries resolved values.
        credential = None
        if isinstance(config, dict):
            config = {k: self._creds.resolve(v) for k, v in config.items()}
            # design name is `credential_ref`; accept `_credential_ref` as a legacy alias
            cred_a = config.pop("credential_ref", None)
            cred_b = config.pop("_credential_ref", None)
            credential = cred_a if cred_a is not None else cred_b
        objects_cfg = config.get("objects", []) if isinstance(config, dict) else []
        state = ConnectorStateStore(self._meta, connector_id)
        ctx = ConnectorContext(state, connector_id, self._ns, object_config_resolver=None)
        if ctype == "file":
            from ...connectors.file.plugin import FileConfig

            plugin = cls(
                FileConfig(
                    root=config["root"],
                    client_id=config.get("client_id", "local"),
                    upload_mode=config.get("upload_mode", False),
                ),
                credential,
                ctx=ctx,
            )
            plugin.file_state = FileStateStore(self._meta, self._ns, connector_id)
        else:
            plugin = cls(config, credential, ctx=ctx)

        # resolver: user [[objects]] match wins;
        # else the connector's built-in preset so SaaS sources are searchable with zero config.
        # Framework-level chunk cap applies unless this object config set its own.
        ctx._resolver = self._build_object_config_resolver(plugin, objects_cfg)
        return BuiltPlugin(plugin, ctx)

    def _build_object_config_resolver(self, plugin, objects_cfg: list):
        """Return a (path) -> ObjectConfig closure, inject it into ctx._resolver.
        Verbatim migration of the closure in ``_build_plugin``."""
        _CHUNK_MAX_DEFAULT = ObjectConfig.__dataclass_fields__["chunk_max"].default

        def _resolve_cfg(p: str) -> ObjectConfig:
            user = _match_object_config(objects_cfg, p)
            if user is not None:
                oc = user
            else:
                preset_key = plugin.preset_for(p)
                oc = (
                    (preset_object_config(preset_key) or ObjectConfig())
                    if preset_key
                    else ObjectConfig()
                )
            # framework-level chunk cap applies unless this object config set its own
            if (
                oc.chunk_max == _CHUNK_MAX_DEFAULT
                and self._cfg.chunking.default_chunk_max != _CHUNK_MAX_DEFAULT
            ):
                oc = _replace(oc, chunk_max=self._cfg.chunking.default_chunk_max)
            return oc

        return _resolve_cfg


# --- ConnectorLocator: read-path longest-prefix match (pure function) ---
class ConnectorLocator:
    """Longest-prefix match against registered connectors (pure function, no SQL).

    The factory never touches SQL; rows are supplied by
    ``ObjectRepository.list_connectors_all()``. Two match paths: a scheme URI (any
    ://) goes by root_uri prefix; a bare local path goes by file://local prefix.
    Verbatim migration of ``_match_connector``.
    """

    @staticmethod
    def match(rows: list[dict], path: str) -> tuple[dict, str] | None:
        """Return (connector_row, relpath) or None."""
        # Any URI (postgres://, web://, file://<client_id><abs>) -> the longest registered root_uri prefix.
        # Covers upload connectors registered under their stable file://<client_id> identity.
        if "://" in path:
            best, best_root = None, ""
            for r in rows:
                ru = r["root_uri"]
                if "://" not in ru:
                    continue
                if path == ru or path.startswith(ru.rstrip("/") + "/"):
                    if len(ru) > len(best_root):
                        best, best_root = r, ru
            if best is None:
                return None
            rel = path[len(best_root) :] or "/"
            if not rel.startswith("/"):
                rel = "/" + rel
            return best, rel
        # bare local filesystem path -> file://local connector whose root is the
        # longest prefix
        abs_path = os.path.abspath(path)
        best, best_root = None, ""
        for r in rows:
            if r["type"] != "file":
                continue
            root_abs = r["root_uri"].replace("file://local", "", 1)
            if abs_path == root_abs or abs_path.startswith(root_abs.rstrip("/") + "/"):
                if len(root_abs) > len(best_root):
                    best, best_root = r, root_abs
        if best is None:
            return None
        rel = "/" if abs_path == best_root else "/" + os.path.relpath(abs_path, best_root)
        return best, rel


# --- ConnectorFactory: the facade ---


class ConnectorFactory:
    """Factory facade: target resolution / credential redact+resolve / plugin build.

    Does NOT touch connectors-table SQL (that's ``ObjectRepository``). To keep this
    change merge-safe, ingest orchestration and the read path stay on ``Engine``;
    the factory is invoked only through thin Engine delegates. ``cfg`` + ``meta``
    are held solely to build ``ConnectorStateStore`` / ``FileStateStore`` (plugin
    dependencies).
    """

    def __init__(self, cfg: ServerConfig, meta: MetadataStore):
        self._cfg = cfg
        self._meta = meta
        self._creds = CredentialService()
        self._builder = PluginBuilder(cfg, meta, self._creds)

    # --- target resolution ---

    def resolve_target(self, target: str) -> TargetResolution:
        """User target URI -> (ctype, connector_uri, scheme, default_config).

        Dispatch: extract the scheme (a bare path defaults to ``file``), look up
        the plugin class via ``registry.get_plugin_cls``, and delegate URI ->
        identity/config derivation to its ``derive_target`` classmethod (defined
        in ``connectors/base.py``; SaaS connectors inherit the default passthrough,
        file/github override). Requires ``registry.load_builtin()`` to have run so
        schemes are registered; the Engine does this in ``startup()``.
        """
        m = _SCHEME_RE.match(target)
        sch = m.group(1) if m else "file"
        cls = get_plugin_cls(sch)
        if cls is None:
            raise NotImplementedError(f"connector scheme '{sch}' not yet implemented")
        ctype, connector_uri, scheme, cfg = cls.derive_target(target)
        return TargetResolution(ctype, connector_uri, scheme, cfg)

    # --- credentials (single security entry point) ---

    def validate_credentials(self, config: Any) -> None:
        """Reject plaintext secrets in a caller-supplied config before it is ever
        redacted/persisted. Callers that register/update a connector MUST call
        this before ``redact``."""
        self._creds.validate_no_plaintext_secrets(config)

    def redact(self, config: Any) -> Any:
        """Recursively redact inline secrets before persistence.
        ObjectRepository MUST call this before writing a connectors row."""
        return self._creds.redact(config)

    def resolve_credential(self, value: Any) -> Any:
        """Resolve env:/file: references before plugin build. Exposed for audit;
        PluginBuilder calls it internally."""
        return self._creds.resolve(value)

    # --- config schema validation ---

    def validate_config(self, ctype: str, config: dict) -> None:
        """Validate a caller-supplied config against the connector's
        CONFIG_SCHEMA (if it declares one), before any connection is
        attempted. Callers registering/probing/estimating a connector with an
        explicit config SHOULD call this — catches a wrong-typed value (e.g. a
        quoted number) or an unrecognized field with one clean, named error
        instead of a raw exception surfacing deep in the plugin's own
        connect()/read path. A connector with no CONFIG_SCHEMA, or one that
        predates ConnectorConfigSchema (FileConfig is a plain @dataclass, not
        a pydantic model, and was never actually wired up to any enforcement
        before this), is unaffected — opt-in per connector, not universal."""
        if not isinstance(config, dict):
            return
        cls = get_plugin_cls(ctype)
        schema = getattr(cls, "CONFIG_SCHEMA", None) if cls is not None else None
        if schema is None or not (
            isinstance(schema, type) and issubclass(schema, ConnectorConfigSchema)
        ):
            return
        try:
            schema(**config)
        except _PydanticValidationError as e:
            raise ValueError(f"config_invalid: {_summarize_validation_error(e)}") from e

    # --- plugin build ---

    def build_plugin(self, ctype: str, config: dict, connector_id: str) -> BuiltPlugin:
        """ctype + stored config + cid -> assembled ctx + resolver-injected plugin.
        credential_ref is popped here (never passed to the plugin constructor); the
        FileConfig special-case lives here."""
        return self._builder.build(ctype, config, connector_id)

    # --- read-path location (match + build + connect) ---

    async def open_path(self, rows: list[dict], path: str) -> ResolvedConnector:
        """Longest-prefix match against registered connectors + rebuild plugin +
        connect. `rows` is supplied by ObjectRepository.list_connectors_all() (the
        factory never queries SQL). Caller owns plugin.close()."""
        match = ConnectorLocator.match(rows, path)
        if match is None:
            raise ValueError(f"path not under any registered connector: {path}")
        row, rel = match
        built = self.build_plugin(row["type"], json.loads(row["config_json"]), row["id"])
        await built.plugin.connect()
        return ResolvedConnector(row["id"], row["root_uri"], rel, built)
