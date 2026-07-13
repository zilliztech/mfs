"""ConnectorFactory component unit tests.

Covers ConnectorFactory.resolve_target (target URI dispatch via each plugin
class's derive_target), CredentialService (redact + resolve — the single
security entry point), PluginBuilder (instantiation + ctx assembly +
object_config resolver), and ConnectorLocator (longest-prefix match). Pure unit
tests — no Milvus / embedding / full Engine. See
``docs-dev/connector-factory-design.md`` §7.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import pytest

from mfs_server.config import ServerConfig
from mfs_server.connectors.base import ConnectorConfigSchema, ConnectorPlugin, ObjectConfig
from mfs_server.connectors.file.plugin import FilePlugin
from mfs_server.connectors.github.plugin import GitHubPlugin
from mfs_server.connectors.registry import load_builtin, register
from mfs_server.engine.components.connector_factory import (
    ConnectorFactory,
    ConnectorLocator,
    CredentialService,
    PluginBuilder,
    _match_object_config,
)


# --- fixtures ---


class _DummyMeta:
    """Meta stand-in: ConnectorStateStore / FileStateStore only store the ref at
    build time; no DB methods are called during PluginBuilder.build / resolve_target."""


@pytest.fixture
def cfg(tmp_path: Path) -> ServerConfig:
    c = ServerConfig()
    c.metadata.backend = "sqlite"
    c.metadata.path = str(tmp_path / "meta.db")
    return c


@pytest.fixture
def builder(cfg: ServerConfig) -> PluginBuilder:
    return PluginBuilder(cfg, _DummyMeta(), CredentialService())


@pytest.fixture
def factory(cfg: ServerConfig) -> ConnectorFactory:
    return ConnectorFactory(cfg, _DummyMeta())


# A fake non-file plugin registered under a unique scheme. register() requires only
# URI_SCHEME; the builder instantiates it as cls(config, credential, ctx=ctx) and the
# resolver calls plugin.preset_for(path).
class _FakePlugin:
    URI_SCHEME = "mfs-fake-test"  # unique; won't collide with real connectors

    def __init__(self, config, credential, ctx=None):
        self.config = config
        self.credential = credential
        self.ctx = ctx

    def preset_for(self, path: str):  # noqa: ARG002
        return None  # no preset -> resolver falls back to default ObjectConfig


register(_FakePlugin)


# A fake plugin carrying a real CONFIG_SCHEMA, for TestValidateConfig. Mirrors
# PostgresConfig's field shape (dsn / max_read_rows) but registers unconditionally --
# unlike postgres, mysql, etc., whose registration is gated behind an optional SDK
# (load_builtin() silently skips them when e.g. asyncpg isn't installed). Exercising
# schema enforcement against a real optional-SDK connector made these tests pass or
# fail depending on which extras happened to be installed, rather than on the
# behavior under test.
class _ValidateConfigSchema(ConnectorConfigSchema):
    dsn: Optional[str] = None
    max_read_rows: int = 100000


class _FakeSchemaPlugin:
    URI_SCHEME = "mfs-fake-validate-config"
    CONFIG_SCHEMA = _ValidateConfigSchema

    def __init__(self, config, credential, ctx=None):
        self.config = config
        self.credential = credential
        self.ctx = ctx


register(_FakeSchemaPlugin)

# ConnectorFactory.resolve_target dispatches via registry.get_plugin_cls, so the
# built-in connectors (file/github at minimum) must be registered before the
# resolve_target tests run. load_builtin() skips connectors whose optional SDK
# isn't installed.
load_builtin()


# ===========================================================================
# §7.1 resolve_target (target URI dispatch)
# ===========================================================================


class TestResolveTarget:
    def test_github_derives_repo(self, factory: ConnectorFactory):
        r = factory.resolve_target("github://owner/repo")
        assert r.ctype == "github"
        assert r.connector_uri == "github://owner/repo"
        assert r.scheme == "github"
        assert r.config == {"repo": "owner/repo"}

    def test_github_tolerates_github_com_prefix(self, factory: ConnectorFactory):
        r = factory.resolve_target("github://github.com/owner/repo")
        assert r.config == {"repo": "owner/repo"}

    def test_github_single_segment_no_crash(self, factory: ConnectorFactory):
        r = factory.resolve_target("github://owner")
        assert r.config == {}

    def test_file_triple_slash_local(self, factory: ConnectorFactory):
        r = factory.resolve_target("file:///abs/path")
        assert r.ctype == "file"
        assert r.connector_uri == f"file://local{os.path.abspath('/abs/path')}"
        assert r.config["root"] == os.path.abspath("/abs/path")
        assert r.config["client_id"] == "local"

    def test_file_local_roundtrip(self, factory: ConnectorFactory):
        r = factory.resolve_target("file://local/abs/path")
        assert r.connector_uri == "file://local/abs/path"
        assert r.config == {"root": "/abs/path", "client_id": "local"}

    def test_file_upload_identity_returns_bare(self, factory: ConnectorFactory):
        r = factory.resolve_target("file://client_x/abs")
        assert r.config == {}
        assert r.connector_uri == "file://client_x/abs"

    def test_bare_local_path(self, factory: ConnectorFactory):
        r = factory.resolve_target("/abs/path")
        assert r.ctype == "file"
        assert r.config["client_id"] == "local"
        assert r.connector_uri == f"file://local{os.path.abspath('/abs/path')}"

    def test_unknown_scheme_raises(self, factory: ConnectorFactory):
        with pytest.raises(NotImplementedError, match="not yet implemented"):
            factory.resolve_target("foo://x")

    def test_default_passthrough_is_inherited(self):
        # A connector that only declares URI_SCHEME inherits the base derive_target:
        # the URI passes through unchanged with an empty config. This is the
        # open/closed proof — a new SaaS connector needs no factory change and no
        # per-scheme registration. Tested on the classmethod directly since the
        # scheme need not be registered to exercise the default.
        class _Passthrough(ConnectorPlugin):
            URI_SCHEME = "x-passthrough"

        assert _Passthrough.derive_target("x-passthrough://r") == (
            "x-passthrough",
            "x-passthrough://r",
            "x-passthrough",
            {},
        )

    def test_github_keeps_repo_derivation(self, factory: ConnectorFactory):
        # github overrides derive_target on the plugin class to derive {repo} from
        # the URI; target derivation is the plugin's own responsibility, not the
        # factory's.
        r = factory.resolve_target("github://owner/repo")
        assert r.config == {"repo": "owner/repo"}

    def test_file_plugin_derive_target_directly(self):
        # The four-form normalization lives on FilePlugin.derive_target; spot-check
        # it directly (independent of factory dispatch).
        assert FilePlugin.derive_target("file://local/abs/path") == (
            "file",
            "file://local/abs/path",
            "file",
            {"root": "/abs/path", "client_id": "local"},
        )

    def test_github_plugin_derive_target_directly(self):
        assert GitHubPlugin.derive_target("github://owner/repo") == (
            "github",
            "github://owner/repo",
            "github",
            {"repo": "owner/repo"},
        )


# ===========================================================================
# §7.2 CredentialService.redact
# ===========================================================================


class TestRedact:
    def test_top_level_secret_keys_redacted(self):
        for k in ("password", "api_key", "refresh_token", "access_key", "client_secret"):
            out = CredentialService.redact({k: "shh"})
            assert out[k] is None, k

    def test_nested_dict_recursive(self):
        out = CredentialService.redact({"oauth": {"access_token": "t", "scope": "read"}})
        assert out["oauth"]["access_token"] is None
        assert out["oauth"]["scope"] == "read"

    def test_list_recursive(self):
        out = CredentialService.redact({"tokens": [{"token": "t"}, {"id": 1}]})
        assert out["tokens"][0]["token"] is None
        assert out["tokens"][1]["id"] == 1

    def test_env_file_refs_preserved(self):
        out = CredentialService.redact({"password": "env:VAR", "key": "file:/secret"})
        assert out["password"] == "env:VAR"
        assert out["key"] == "file:/secret"

    def test_non_secret_plaintext_kept(self):
        out = CredentialService.redact({"host": "db.example.com", "port": 5432})
        assert out == {"host": "db.example.com", "port": 5432}

    def test_value_level_connection_string_redacted(self):
        out = CredentialService.redact({"url": "postgres://u:p@host/db"})
        assert out["url"] is None

    def test_plain_url_not_redacted(self):
        out = CredentialService.redact({"url": "https://example.com/path"})
        assert out["url"] == "https://example.com/path"

    def test_empty_values_not_replaced(self):
        for v in (None, "", [], {}):
            out = CredentialService.redact({"password": v})
            assert out["password"] == v

    def test_is_secret_key_case_insensitive(self):
        assert CredentialService.is_secret_key("API_KEY")
        assert CredentialService.is_secret_key("api_key")
        assert CredentialService.is_secret_key("RefreshToken")
        assert not CredentialService.is_secret_key("host")

    def test_unimplemented_scheme_under_secret_key_redacted(self):
        out = CredentialService.redact({"password": "secret:foo"})
        assert out["password"] is None
        out2 = CredentialService.redact({"token": "vault:bar"})
        assert out2["token"] is None


# ===========================================================================
# §7.3 CredentialService.resolve
# ===========================================================================


class TestResolve:
    def test_env_var_resolved(self, monkeypatch):
        monkeypatch.setenv("MFS_TEST_VAR", "secret-value")
        assert CredentialService.resolve("env:MFS_TEST_VAR") == "secret-value"

    def test_env_missing_raises(self, monkeypatch):
        monkeypatch.delenv("MFS_TEST_MISSING", raising=False)
        with pytest.raises(ValueError, match="environment variable"):
            CredentialService.resolve("env:MFS_TEST_MISSING")

    def test_file_resolved(self, tmp_path: Path):
        p = tmp_path / "secret"
        p.write_text("  file-value\n")
        assert CredentialService.resolve(f"file:{p}") == "file-value"

    def test_file_missing_raises(self):
        with pytest.raises(ValueError, match="cannot read secret file"):
            CredentialService.resolve("file:/no/such/path/here")

    def test_unimplemented_scheme_raises(self):
        with pytest.raises(ValueError, match="not implemented"):
            CredentialService.resolve("secret:foo")
        with pytest.raises(ValueError, match="not implemented"):
            CredentialService.resolve("vault:bar")

    def test_non_string_passthrough(self):
        assert CredentialService.resolve(123) == 123
        assert CredentialService.resolve(None) is None
        assert CredentialService.resolve(["a"]) == ["a"]

    def test_no_prefix_passthrough(self):
        assert CredentialService.resolve("plain-value") == "plain-value"

    def test_redaction_placeholder_rejected(self):
        with pytest.raises(ValueError, match="redaction placeholder"):
            CredentialService.resolve(CredentialService._REDACTED)


# ===========================================================================
# §7.1b CredentialService.validate_no_plaintext_secrets
# ===========================================================================


class TestValidateNoPlaintextSecrets:
    def test_plaintext_secret_key_rejected(self):
        with pytest.raises(ValueError, match="plaintext secret"):
            CredentialService.validate_no_plaintext_secrets({"password": "shh"})

    def test_plaintext_connection_string_rejected(self):
        with pytest.raises(ValueError, match="plaintext secret"):
            CredentialService.validate_no_plaintext_secrets({"uri": "postgres://u:p@host/db"})

    def test_nested_plaintext_secret_rejected(self):
        with pytest.raises(ValueError, match="plaintext secret"):
            CredentialService.validate_no_plaintext_secrets({"oauth": {"access_token": "t"}})

    def test_env_and_file_refs_accepted(self):
        CredentialService.validate_no_plaintext_secrets({"password": "env:VAR", "key": "file:/p"})

    def test_non_secret_plaintext_accepted(self):
        CredentialService.validate_no_plaintext_secrets({"host": "db.example.com", "port": 5432})

    def test_empty_values_accepted(self):
        for v in (None, "", [], {}):
            CredentialService.validate_no_plaintext_secrets({"password": v})


# ===========================================================================
# ConnectorFactory.validate_config — CONFIG_SCHEMA enforcement at add/update/
# probe time, before any connection is attempted. Covers the 3 failure shapes
# a schema-less config-parse path let through: a wrong-typed field, a bad-type
# field with no coercion path, and a silently-ignored unknown field.
# ===========================================================================


class TestValidateConfig:
    # Uses the fake "mfs-fake-validate-config" scheme registered above, not a real
    # connector like postgres -- postgres's own registration is gated behind the
    # optional asyncpg SDK (load_builtin() skips it silently when absent), so
    # exercising schema enforcement through it made these tests pass or fail
    # depending on which extras happened to be installed rather than on the
    # behavior under test. The fake schema mirrors PostgresConfig's field shape
    # (dsn / max_read_rows) so the cases below still cover the same 3 failure
    # shapes; postgres's real CONFIG_SCHEMA is unaffected and still enforced in
    # production, just not re-exercised here.
    CTYPE = "mfs-fake-validate-config"

    def test_valid_config_passes(self, factory: ConnectorFactory):
        factory.validate_config(self.CTYPE, {"dsn": "env:PG_DSN", "max_read_rows": 500})

    def test_numeric_field_as_string_is_rejected(self, factory: ConnectorFactory):
        # ConnectorConfigSchema sets strict=True: a quoted number ("100") must be
        # rejected outright, not silently coerced. Lax coercion only fixed the
        # *schema's* type -- validate_config validates a throwaway schema
        # instance and never writes the coerced value back into the config
        # dict a plugin actually reads at runtime, so a quoted string used to
        # sail through validation and still reach read_records()'s `> lim`
        # comparison as a str, crashing with TypeError deep in the plugin.
        with pytest.raises(ValueError, match="config_invalid.*max_read_rows"):
            factory.validate_config(self.CTYPE, {"max_read_rows": "100"})

    def test_wrong_type_with_no_coercion_path_is_rejected(self, factory: ConnectorFactory):
        # A bool has no sensible coercion to the str `dsn` expects -- unlike the
        # numeric-string case above, this should be a clean, named rejection
        # instead of a raw exception surfacing deep in the plugin's own
        # connect()/read path.
        with pytest.raises(ValueError, match="config_invalid.*dsn"):
            factory.validate_config(self.CTYPE, {"dsn": True})

    def test_unrecognized_field_is_rejected(self, factory: ConnectorFactory):
        with pytest.raises(ValueError, match="config_invalid.*bogus_field"):
            factory.validate_config(self.CTYPE, {"dsn": "env:PG_DSN", "bogus_field": "nonsense"})

    def test_credential_ref_and_legacy_alias_and_objects_are_allowed(
        self, factory: ConnectorFactory
    ):
        # Cross-cutting fields every connector config may carry regardless of
        # type -- declared once on ConnectorConfigSchema, not per-connector.
        factory.validate_config(self.CTYPE, {"dsn": "env:PG_DSN", "credential_ref": "env:X"})
        factory.validate_config(self.CTYPE, {"dsn": "env:PG_DSN", "_credential_ref": "env:X"})
        factory.validate_config(
            self.CTYPE, {"dsn": "env:PG_DSN", "objects": [{"match": "*.csv", "priority": 1}]}
        )

    def test_connector_without_config_schema_is_unaffected(self, factory: ConnectorFactory):
        # file's CONFIG_SCHEMA (FileConfig) predates ConnectorConfigSchema and is
        # a plain @dataclass, not a pydantic model -- validate_config must not
        # try to enforce it (it was never wired up to any enforcement before
        # this, and objects/credential_ref would break its narrow field set).
        factory.validate_config("file", {"root": "/tmp/x", "objects": [{"match": "*"}]})

    def test_non_dict_config_is_a_noop(self, factory: ConnectorFactory):
        factory.validate_config(self.CTYPE, None)

    def test_unknown_ctype_is_a_noop(self, factory: ConnectorFactory):
        factory.validate_config("no-such-connector-type", {"anything": 1})


# ===========================================================================
# §7.4 PluginBuilder.build
# ===========================================================================


class TestPluginBuilder:
    def test_file_ctype_attaches_file_state(self, builder: PluginBuilder, tmp_path: Path):
        from mfs_server.connectors.file.plugin import FilePlugin
        from mfs_server.storage.file_state import FileStateStore

        root = tmp_path / "root"
        root.mkdir()
        built = builder.build("file", {"root": str(root), "client_id": "local"}, "cid-f")
        assert isinstance(built.plugin, FilePlugin)
        assert isinstance(built.plugin.file_state, FileStateStore)
        assert built.ctx.connector_id == "cid-f"

    def test_non_file_pops_credential_ref(self, builder: PluginBuilder):
        # A plaintext credential_ref (no env:/file: prefix) passes through resolve
        # unchanged; the key itself must be popped from the config handed to the plugin.
        built = builder.build("mfs-fake-test", {"a": 1, "credential_ref": "plain-token"}, "cid")
        assert "credential_ref" not in built.plugin.config
        assert "_credential_ref" not in built.plugin.config
        assert built.plugin.credential == "plain-token"

    def test_credential_ref_env_resolved(self, builder: PluginBuilder, monkeypatch):
        monkeypatch.setenv("MFS_PLUG_TOKEN", "tok")
        built = builder.build(
            "mfs-fake-test", {"a": 1, "credential_ref": "env:MFS_PLUG_TOKEN"}, "cid"
        )
        assert built.plugin.credential == "tok"
        assert "credential_ref" not in built.plugin.config

    def test_legacy_credential_ref_alias(self, builder: PluginBuilder, monkeypatch):
        monkeypatch.setenv("MFS_PLUG_TOKEN2", "tok2")
        built = builder.build(
            "mfs-fake-test", {"a": 1, "_credential_ref": "env:MFS_PLUG_TOKEN2"}, "cid"
        )
        assert built.plugin.credential == "tok2"
        assert "_credential_ref" not in built.plugin.config

    def test_unknown_ctype_raises(self, builder: PluginBuilder):
        with pytest.raises(NotImplementedError, match="no plugin"):
            builder.build("no-such-ctype", {}, "cid")

    def test_object_config_resolver_user_match_wins(self, builder: PluginBuilder):
        built = builder.build(
            "mfs-fake-test",
            {"objects": [{"match": "*.md", "chunk_max": 99}]},
            "cid",
        )
        oc = built.ctx.object_config_for("/foo.md")
        assert oc.chunk_max == 99

    def test_object_config_resolver_default_when_no_match(self, builder: PluginBuilder):
        built = builder.build("mfs-fake-test", {"objects": []}, "cid")
        oc = built.ctx.object_config_for("/foo.md")
        assert isinstance(oc, ObjectConfig)
        assert oc.chunk_max == ObjectConfig.__dataclass_fields__["chunk_max"].default

    def test_object_config_resolver_chunk_max_cap(self, cfg: ServerConfig):
        # When the user didn't set chunk_max AND cfg.default_chunk_max differs from
        # the ObjectConfig default, the framework cap applies.
        cfg.chunking.default_chunk_max = 500
        b = PluginBuilder(cfg, _DummyMeta(), CredentialService())
        built = b.build("mfs-fake-test", {"objects": []}, "cid")
        oc = built.ctx.object_config_for("/anything")
        assert oc.chunk_max == 500


# ===========================================================================
# §7.5 ConnectorLocator.match
# ===========================================================================


def _row(cid: str, root_uri: str, ctype: str = "file", config_json: str = "{}") -> dict:
    return {"id": cid, "root_uri": root_uri, "type": ctype, "config_json": config_json}


class TestConnectorLocator:
    def test_scheme_uri_longest_prefix(self):
        rows = [
            _row("1", "github://owner/repo"),
            _row("2", "github://owner/repo/src"),
        ]
        m = ConnectorLocator.match(rows, "github://owner/repo/src/main.py")
        assert m is not None
        assert m[0]["id"] == "2"
        assert m[1] == "/main.py"

    def test_scheme_uri_exact_root_relpath_slash(self):
        rows = [_row("1", "postgres://db")]
        m = ConnectorLocator.match(rows, "postgres://db")
        assert m is not None
        assert m[1] == "/"

    def test_bare_local_path_longest_prefix(self, tmp_path: Path):
        root = tmp_path / "root"
        nested = root / "sub"
        root.mkdir()
        nested.mkdir()
        rows = [
            _row("1", f"file://local{root}"),
            _row("2", f"file://local{nested}"),
        ]
        m = ConnectorLocator.match(rows, str(nested / "a.txt"))
        assert m is not None
        assert m[0]["id"] == "2"
        assert m[1] == "/a.txt"

    def test_bare_local_exact_root(self, tmp_path: Path):
        root = tmp_path / "root"
        root.mkdir()
        rows = [_row("1", f"file://local{root}")]
        m = ConnectorLocator.match(rows, str(root))
        assert m is not None
        assert m[1] == "/"

    def test_no_match_returns_none(self):
        rows = [_row("1", "github://owner/repo")]
        assert ConnectorLocator.match(rows, "postgres://other") is None

    def test_upload_identity_matches_registered_row(self):
        rows = [_row("1", "file://client_x/abs")]
        m = ConnectorLocator.match(rows, "file://client_x/abs/sub")
        assert m is not None
        assert m[0]["id"] == "1"
        assert m[1] == "/sub"


# ===========================================================================
# _match_object_config (module-level helper)
# ===========================================================================


class TestMatchObjectConfig:
    def test_first_match_wins(self):
        cfgs = [{"match": "*.md", "chunk_max": 1}, {"match": "*", "chunk_max": 2}]
        oc = _match_object_config(cfgs, "/foo.md")
        assert oc.chunk_max == 1

    def test_no_match_returns_none(self):
        assert _match_object_config([], "/foo") is None
        assert _match_object_config([{"match": "*.md"}], "/foo.txt") is None

    def test_strips_match_key(self):
        oc = _match_object_config([{"match": "*.md", "chunk_max": 7}], "/foo.md")
        assert oc is not None
        assert oc.chunk_max == 7
