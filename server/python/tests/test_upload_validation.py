from __future__ import annotations

import io
import tarfile

import pytest

from mfs_server.config import ServerConfig
from mfs_server.engine.engine import Engine


def _tar_bytes(entries: list[tuple[str, bytes | str, str]]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, payload, kind in entries:
            if kind == "symlink":
                ti = tarfile.TarInfo(name)
                ti.type = tarfile.SYMTYPE
                ti.linkname = str(payload)
                tf.addfile(ti)
                continue
            data = payload if isinstance(payload, bytes) else payload.encode()
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            ti.mode = 0o644
            tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


async def _engine(tmp_path) -> Engine:
    cfg = ServerConfig()
    cfg.metadata.backend = "sqlite"
    cfg.metadata.path = str(tmp_path / "metadata.db")
    cfg.transformation_cache.backend = "sqlite"
    cfg.transformation_cache.db_path = str(tmp_path / "tx.db")
    cfg.artifact_cache.root = str(tmp_path / "artifacts")
    cfg.milvus.uri = str(tmp_path / "milvus.db")
    eng = Engine(cfg)
    await eng.meta.connect()
    await eng.meta.init_schema()
    return eng


async def _assert_no_ingest_side_effects(eng: Engine, tmp_path) -> None:
    for table in ("connectors", "connector_jobs", "file_state", "object_tasks"):
        row = await eng.meta.fetchone(f"SELECT count(*) AS n FROM {table}")
        assert row["n"] == 0
    assert not (tmp_path / "artifacts" / "files").exists()


@pytest.mark.parametrize(
    ("bundle", "match"),
    [
        (_tar_bytes([("../escape.txt", b"escape", "file")]), "unsafe path"),
        (_tar_bytes([("/abs.txt", b"absolute", "file")]), "unsafe path"),
        (_tar_bytes([("link", "target.txt", "symlink")]), "links not allowed"),
    ],
    ids=["traversal", "absolute", "symlink"],
)
async def test_legacy_upload_rejects_unsafe_tar_before_metadata_side_effects(
    tmp_path, bundle, match
) -> None:
    eng = await _engine(tmp_path)
    try:
        with pytest.raises(ValueError, match=match):
            await eng.ingest_upload("bad-upload", bundle, process=False)
        await _assert_no_ingest_side_effects(eng, tmp_path)
    finally:
        await eng.meta.close()


@pytest.mark.parametrize(
    ("bundle", "match"),
    [
        (
            _tar_bytes(
                [
                    (".mfs-meta.json", b'{"hashes":[],"renames":[],"deletions":[]}', "file"),
                    ("../escape.txt", b"escape", "file"),
                ]
            ),
            "unsafe path",
        ),
        (
            _tar_bytes(
                [
                    (".mfs-meta.json", b'{"hashes":[],"renames":[],"deletions":[]}', "file"),
                    ("link", "target.txt", "symlink"),
                ]
            ),
            "links not allowed",
        ),
    ],
    ids=["traversal", "symlink"],
)
async def test_manifest_upload_rejects_unsafe_tar_before_metadata_side_effects(
    tmp_path, bundle, match
) -> None:
    eng = await _engine(tmp_path)
    try:
        with pytest.raises(ValueError, match=match):
            await eng.files_upload("client-id", "/tmp/source", bundle, process=False)
        await _assert_no_ingest_side_effects(eng, tmp_path)
    finally:
        await eng.meta.close()
