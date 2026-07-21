"""Independent unit tests for UploadService + helpers: constructed without an
Engine, without real meta/ingest, injecting fakes. Covers the pure helpers
(_norm_rel / _validate_upload_member) and the staging locator (_staging_root /
_staging_connector).
"""

from __future__ import annotations

import hashlib
import tarfile
from types import SimpleNamespace

import pytest

from mfs_server.config import ServerConfig
from mfs_server.engine.components.upload import UploadService, _norm_rel, _validate_upload_member


# --- pure helpers ---


def test_norm_rel_single_leading_slash():
    assert _norm_rel("a/b") == "/a/b"
    assert _norm_rel("/a/b") == "/a/b"
    assert _norm_rel("//a") == "/a"
    assert _norm_rel("a") == "/a"


def _tinfo(name: str, type: int = tarfile.REGTYPE) -> tarfile.TarInfo:
    t = tarfile.TarInfo(name)
    t.type = type
    return t


def test_validate_upload_member_accepts_regular_file():
    _validate_upload_member(_tinfo("foo/bar.txt"))  # no raise


def test_validate_upload_member_accepts_dir():
    _validate_upload_member(_tinfo("foo/", type=tarfile.DIRTYPE))  # no raise


def test_validate_upload_member_rejects_symlink():
    with pytest.raises(ValueError, match="links not allowed"):
        _validate_upload_member(_tinfo("x", type=tarfile.SYMTYPE))


def test_validate_upload_member_rejects_hardlink():
    with pytest.raises(ValueError, match="links not allowed"):
        _validate_upload_member(_tinfo("x", type=tarfile.LNKTYPE))


def test_validate_upload_member_rejects_absolute_path():
    with pytest.raises(ValueError, match="unsafe path"):
        _validate_upload_member(_tinfo("/etc/passwd"))


def test_validate_upload_member_rejects_traversal():
    with pytest.raises(ValueError, match="unsafe path"):
        _validate_upload_member(_tinfo("../escape"))


# --- UploadService construction + staging locator (fakes, no Engine/meta) ---


class _FakeArtifactCache:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def files_root(self, ns: str, sub: str) -> str:
        self.calls.append((ns, sub))
        return f"/tmp/staging/{ns}/{sub}"


class _FakeIngest:
    def __init__(self):
        self.register_calls: list[tuple] = []

    async def register_or_get_connector(self, uri, ctype, config):
        self.register_calls.append((uri, ctype, config))
        return "cid-fake"


def _build_upload():
    cfg = ServerConfig()
    infra = SimpleNamespace(artifact_cache=_FakeArtifactCache(), meta=SimpleNamespace())
    return UploadService(cfg, infra, SimpleNamespace(), _FakeIngest())


def test_staging_root_calls_files_root_with_sha1_sub():
    up = _build_upload()
    root = up._staging_root("client-1", "/repo")
    expected_sub = hashlib.sha1("client-1:/repo".encode()).hexdigest()[:16]
    assert up._infra.artifact_cache.calls[-1][1] == expected_sub
    assert isinstance(root, str)
    assert "staging" in root


async def test_staging_connector_registers_file_connector_with_stable_uri():
    up = _build_upload()
    staging, connector_uri, cid = await up._staging_connector("client-1", "/repo")
    assert cid == "cid-fake"
    assert connector_uri == "file://client-1/repo"
    assert up._ingest.register_calls == [
        (
            "file://client-1/repo",
            "file",
            {
                "root": staging,
                "client_id": "client-1",
                "upload_mode": True,
            },
        )
    ]
