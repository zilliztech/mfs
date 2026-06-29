"""ConnectorFactory component unit tests.

Covers TargetResolver (scheme registry dispatch), CredentialService (redact +
resolve — the single security entry point), PluginBuilder (instantiation + ctx
assembly + object_config resolver), and ConnectorLocator (longest-prefix match).
Pure unit tests — no Milvus / embedding / full Engine. See
``docs-dev/connector-factory-design.md`` §7.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mfs_server.config import ServerConfig
from mfs_server.connectors.base import ObjectConfig
from mfs_server.connectors.registry import register
from mfs_server.engine.components.connector_factory import (
    ConnectorLocator,
    CredentialService,
    PluginBuilder,
    TargetResolution,
    TargetResolver,
    _match_object_config,
)


# --- fixtures ---


class _DummyMeta:
    """Meta stand-in: ConnectorStateStore / FileStateStore only store the ref at
    build time; no DB methods are called during PluginBuilder.build."""


@pytest.fixture
def cfg(tmp_path: Path) -> ServerConfig:
    c = ServerConfig()
    c.metadata.backend = "sqlite"
    c.metadata.path = str(tmp_path / "meta.db")
    return c


@pytest.fixture
def builder(cfg: ServerConfig) -> PluginBuilder:
    return PluginBuilder(cfg, _DummyMeta(), CredentialService())


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


# ===========================================================================
# §7.1 TargetResolver
# ===========================================================================


class TestTargetResolver:
    def test_github_derives_repo(self):
        r = TargetResolver().resolve("github://owner/repo")
        assert r.ctype == "github"
        assert r.connector_uri == "github://owner/repo"
        assert r.scheme == "github"
        assert r.config == {"repo": "owner/repo"}

    def test_github_tolerates_github_com_prefix(self):
        r = TargetResolver().resolve("github://github.com/owner/repo")
        assert r.config == {"repo": "owner/repo"}

    def test_github_single_segment_no_crash(self):
        r = TargetResolver().resolve("github://owner")
        assert r.config == {}

    def test_file_triple_slash_local(self):
        r = TargetResolver().resolve("file:///abs/path")
        assert r.ctype == "file"
        assert r.connector_uri == f"file://local{os.path.abspath('/abs/path')}"
        assert r.config["root"] == os.path.abspath("/abs/path")
        assert r.config["client_id"] == "local"

    def test_file_local_roundtrip(self):
        r = TargetResolver().resolve("file://local/abs/path")
        assert r.connector_uri == "file://local/abs/path"
        assert r.config == {"root": "/abs/path", "client_id": "local"}

    def test_file_upload_identity_returns_bare(self):
        r = TargetResolver().resolve("file://client_x/abs")
        assert r.config == {}
        assert r.connector_uri == "file://client_x/abs"

    def test_bare_local_path(self):
        r = TargetResolver().resolve("/abs/path")
        assert r.ctype == "file"
        assert r.config["client_id"] == "local"
        assert r.connector_uri == f"file://local{os.path.abspath('/abs/path')}"

    @pytest.mark.parametrize(
        "scheme",
        [
            "web",
            "postgres",
            "mysql",
            "mongo",
            "slack",
            "discord",
            "gmail",
            "notion",
            "jira",
            "linear",
            "zendesk",
            "hubspot",
            "bigquery",
            "snowflake",
            "s3",
            "gdrive",
            "feishu",
        ],
    )
    def test_saas_passthrough(self, scheme: str):
        target = f"{scheme}://resource"
        r = TargetResolver().resolve(target)
        assert r.ctype == scheme
        assert r.connector_uri == target
        assert r.config == {}

    def test_unknown_scheme_raises(self):
        with pytest.raises(NotImplementedError, match="not yet implemented"):
            TargetResolver().resolve("foo://x")

    def test_register_new_scheme_zero_code_change(self):
        # The open/closed proof: registering a new scheme dispatches it without
        # touching the factory body.
        TargetResolver.register(
            "x-test-scheme", lambda raw: TargetResolution("x", raw, "x", {"k": 1})
        )
        r = TargetResolver().resolve("x-test-scheme://a")
        assert r.ctype == "x" and r.config == {"k": 1}

    def test_github_not_overwritten_by_saas_loop(self):
        # The P0 regression: github must keep its repo-derivation resolver and not be
        # clobbered by the SaaS passthrough registration.
        r = TargetResolver().resolve("github://owner/repo")
        assert r.config == {"repo": "owner/repo"}, "github resolver was overwritten by SaaS loop"


# ===========================================================================
# §7.2 CredentialService.redact
# ===========================================================================


class TestRedact:
    def test_top_level_secret_keys_redacted(self):
        for k in ("password", "api_key", "refresh_token", "access_key", "client_secret"):
            out = CredentialService.redact({k: "shh"})
            assert out[k] == CredentialService._REDACTED, k

    def test_nested_dict_recursive(self):
        out = CredentialService.redact({"oauth": {"access_token": "t", "scope": "read"}})
        assert out["oauth"]["access_token"] == CredentialService._REDACTED
        assert out["oauth"]["scope"] == "read"

    def test_list_recursive(self):
        out = CredentialService.redact({"tokens": [{"token": "t"}, {"id": 1}]})
        assert out["tokens"][0]["token"] == CredentialService._REDACTED
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
        assert out["url"] == CredentialService._REDACTED

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
        assert out["password"] == CredentialService._REDACTED
        out2 = CredentialService.redact({"token": "vault:bar"})
        assert out2["token"] == CredentialService._REDACTED


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
