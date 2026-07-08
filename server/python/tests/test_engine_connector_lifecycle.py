from __future__ import annotations

import pytest

from mfs_server.config import ServerConfig
from mfs_server.engine.engine import Engine
from mfs_server.connectors.registry import load_builtin


class _NoopMilvus:
    def delete_by_connector(self, *args, **kwargs):
        return None


class _NoopReduce:
    def register_job(self, *args, **kwargs):
        return None

    def on_sync_done(self, *args, **kwargs):
        return None

    def evict_job(self, *args, **kwargs):
        return None

    def on_yield_object_change(self, *args, **kwargs):
        return None


async def _build_engine(tmp_path) -> Engine:
    load_builtin()
    cfg = ServerConfig()
    cfg.metadata.backend = "sqlite"
    cfg.metadata.path = str(tmp_path / "metadata.db")
    cfg.transformation_cache.backend = "sqlite"
    cfg.transformation_cache.db_path = str(tmp_path / "tx.db")
    cfg.artifact_cache.root = str(tmp_path / "artifacts")
    cfg.milvus.uri = str(tmp_path / "milvus.db")
    eng = Engine(cfg)
    eng.infra.milvus = _NoopMilvus()
    eng.pipeline.job_lane = _NoopReduce()
    await eng.infra.meta.connect()
    await eng.infra.meta.init_schema()
    return eng


async def test_remove_rejects_registered_child_path(tmp_path):
    eng = await _build_engine(tmp_path)
    try:
        root = tmp_path / "repo"
        (root / "src").mkdir(parents=True)
        root_uri = f"file://local{root}"
        await eng.register_or_get_connector(
            root_uri,
            "file",
            {"root": str(root), "client_id": "local"},
        )

        with pytest.raises(ValueError, match="remove_requires_connector_root"):
            await eng.remove_connector(str(root / "src"))
    finally:
        await eng.infra.meta.close()


async def test_remove_rejects_unregistered_target(tmp_path):
    eng = await _build_engine(tmp_path)
    try:
        with pytest.raises(ValueError, match="remove_requires_connector_root"):
            await eng.remove_connector(str(tmp_path / "missing"))
    finally:
        await eng.infra.meta.close()


async def test_failed_initial_add_rolls_back_connector_registration(tmp_path):
    eng = await _build_engine(tmp_path)
    try:
        missing = tmp_path / "missing"

        with pytest.raises(ValueError, match="does not exist"):
            await eng.add(str(missing), process=False)

        for table in ("connectors", "connector_jobs", "object_tasks", "file_state"):
            row = await eng.infra.meta.fetchone(f"SELECT count(*) AS n FROM {table}")
            assert row["n"] == 0
    finally:
        await eng.infra.meta.close()


# ----------------------------------------------------------------------
# add()/register_or_get_connector — omitted --config on an already-registered
# connector must never silently persist a drifted, URI-derived default over the
# real stored config. Previously, `mfs connector update <uri>` with no
# --config would silently and permanently wipe the connector's stored
# credentials whenever the URI alone couldn't reconstruct them (postgres,
# mysql, mongo, s3, web).
# ----------------------------------------------------------------------


async def _seed_file_root(tmp_path, name="repo"):
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    (root / "a.md").write_text("hello")
    return root


async def test_add_without_config_on_drifted_connector_is_rejected(tmp_path):
    eng = await _build_engine(tmp_path)
    try:
        root = await _seed_file_root(tmp_path)
        target = str(root)

        # initial registration with an explicit config whose client_id differs from
        # what derive_target would reconstruct from the bare URI alone ("local") —
        # this is the "real stored config" a later bare re-sync must not clobber.
        await eng.add(target, config={"client_id": "custom"}, process=False)
        row = await eng.objects.get_connector_id_and_config_by_uri(f"file://local{root}")
        before = row["config_json"]
        assert '"custom"' in before

        with pytest.raises(ValueError, match="config_required"):
            await eng.add(target, config=None, process=False)

        after = (await eng.objects.get_connector_id_and_config_by_uri(f"file://local{root}"))[
            "config_json"
        ]
        assert after == before
    finally:
        await eng.infra.meta.close()


async def test_connector_update_without_config_on_drifted_connector_is_rejected(tmp_path):
    """Same as above but through the `mfs connector update` path (update_config=True)."""
    eng = await _build_engine(tmp_path)
    try:
        root = await _seed_file_root(tmp_path)
        target = str(root)

        await eng.add(target, config={"client_id": "custom"}, process=False)
        row = await eng.objects.get_connector_id_and_config_by_uri(f"file://local{root}")
        before = row["config_json"]

        with pytest.raises(ValueError, match="config_required"):
            await eng.add(target, config=None, update_config=True, process=False)

        after = (await eng.objects.get_connector_id_and_config_by_uri(f"file://local{root}"))[
            "config_json"
        ]
        assert after == before
    finally:
        await eng.infra.meta.close()


async def test_add_with_explicit_differing_config_still_persists(tmp_path):
    """No regression: an actual --config that differs from the stored one must still
    persist, unaffected by the new config_explicit guard."""
    eng = await _build_engine(tmp_path)
    try:
        root = await _seed_file_root(tmp_path)
        target = str(root)

        job_id = await eng.add(target, config={"client_id": "custom"}, process=False)
        await eng.cancel_job(job_id)  # free the one-in-flight-sync slot for the 2nd add()
        await eng.add(target, config={"client_id": "custom2"}, process=False)

        row = await eng.objects.get_connector_id_and_config_by_uri(f"file://local{root}")
        assert '"custom2"' in row["config_json"]
    finally:
        await eng.infra.meta.close()


async def test_add_without_config_on_undrifted_connector_is_a_silent_noop(tmp_path):
    """No regression: when the URI-derived default happens to exactly match the
    stored config (the minimal file:// case), a bare re-sync stays a safe no-op."""
    eng = await _build_engine(tmp_path)
    try:
        root = await _seed_file_root(tmp_path)
        target = str(root)

        job_id = await eng.add(target, config=None, process=False)
        row = await eng.objects.get_connector_id_and_config_by_uri(f"file://local{root}")
        before = row["config_json"]

        await eng.cancel_job(job_id)  # free the one-in-flight-sync slot for the 2nd add()
        await eng.add(target, config=None, process=False)

        after = (await eng.objects.get_connector_id_and_config_by_uri(f"file://local{root}"))[
            "config_json"
        ]
        assert after == before
    finally:
        await eng.infra.meta.close()


async def test_add_without_config_on_brand_new_connector_is_unaffected(tmp_path):
    """Brand-new registration (no existing row) never hits the config_explicit guard."""
    eng = await _build_engine(tmp_path)
    try:
        root = await _seed_file_root(tmp_path)
        target = str(root)

        job_id = await eng.add(target, config=None, process=False)
        assert job_id

        row = await eng.objects.get_connector_id_and_config_by_uri(f"file://local{root}")
        assert row is not None
    finally:
        await eng.infra.meta.close()
