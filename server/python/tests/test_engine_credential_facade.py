"""Engine credential-facade backward-compat.

``Engine._is_secret_key`` / ``_redact_config`` / ``_resolve_ref`` are thin class/
static delegates over ``CredentialService`` kept on ``Engine`` for call-site
compatibility. They must stay callable on the class itself (``Engine._is_secret_key``
was once a classmethod; dropping the decorator made ``Engine._is_secret_key("api_key")``
raise ``TypeError: missing 1 required positional argument: key``). These calls need
no infra (no DB / Milvus / startup): they forward to static/class methods, so the
class-level call is both the regression signature and a sufficient check.
"""

from __future__ import annotations

from pathlib import Path

from mfs_server.engine.engine import Engine


def test_is_secret_key_callable_on_class():
    # Regression: without @classmethod, Engine._is_secret_key("api_key") raised
    # TypeError: missing 1 required positional argument: 'key'.
    assert Engine._is_secret_key("api_key") is True
    assert Engine._is_secret_key("API_KEY") is True
    assert Engine._is_secret_key("host") is False


def test_redact_config_callable_on_class():
    assert Engine._redact_config({"api_key": "x"}, key_is_secret=True) == {"api_key": None}
    # a credential reference is kept, not redacted
    assert Engine._redact_config("env:VAR", key_is_secret=True) == "env:VAR"


def test_resolve_ref_callable_on_class(monkeypatch, tmp_path: Path):
    # non-string passthrough
    assert Engine._resolve_ref({"k": 1}) == {"k": 1}
    # env: ref resolves through the facade
    monkeypatch.setenv("MFS_FACADE_TEST", "secret-value")
    assert Engine._resolve_ref("env:MFS_FACADE_TEST") == "secret-value"
    # file: ref resolves through the facade
    f = tmp_path / "secret.txt"
    f.write_text("file-value\n")
    assert Engine._resolve_ref(f"file:{f}") == "file-value"


def test_facade_matches_credential_service():
    # The facade must forward identically to the service it wraps.
    from mfs_server.engine.components import CredentialService

    assert Engine._is_secret_key("token") == CredentialService.is_secret_key("token")
    assert Engine._redact_config({"token": "v"}, True) == CredentialService.redact(
        {"token": "v"}, True
    )
    assert Engine._resolve_ref("plain") == CredentialService.resolve("plain")
