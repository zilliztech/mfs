"""End-to-end: a user `[[objects]] priority=` override changes the processing order of a
real `add()` on a real file connector — drives the actual `plugin.sync()` enumeration +
`_drain_job` task insertion, no fakes on that path. Verifies both that the override lands
on the right objects and that untouched objects still fall back to the connector's own
`task_priority()` buckets (§6.3), and that the DB claim order (priority ASC, existing
behavior) reflects it end to end."""

from __future__ import annotations

from mfs_server.config import ServerConfig
from mfs_server.connectors.registry import load_builtin
from mfs_server.engine.engine import Engine


class _NoopMilvus:
    def delete_by_connector(self, *args, **kwargs):
        return None


class _NoopJobLane:
    def register_job(self, *args, **kwargs):
        return None

    def on_sync_done(self, *args, **kwargs):
        return None

    def on_yield_object_change(self, *args, **kwargs):
        return None

    def evict_job(self, *args, **kwargs):
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
    eng.pipeline._job_lane = _NoopJobLane()
    await eng.infra.meta.connect()
    await eng.infra.meta.init_schema()
    return eng


async def test_objects_priority_override_wins_and_falls_back_end_to_end(tmp_path):
    eng = await _build_engine(tmp_path)
    try:
        root = tmp_path / "repo"
        (root / "src").mkdir(parents=True)
        (root / "archive").mkdir()
        (root / "README.md").write_text("entrypoint")
        (root / "notes.md").write_text("scratch notes")
        (root / "src" / "main.py").write_text("print(1)")
        (root / "archive" / "old.txt").write_text("stale")

        root_uri = f"file://local{root}"
        await eng.add(
            str(root),
            config={
                "root": str(root),
                "client_id": "local",
                "objects": [
                    # user override: bump archive/* to the back of the line
                    {"match": "archive/*", "priority": 900},
                    # user override: pull notes.md ahead of everything, including
                    # the file connector's own -350 entrypoint bucket
                    {"match": "notes.md", "priority": -500},
                ],
            },
            process=False,  # enumerate + insert_task only; no embedding/Milvus needed
        )

        cid = await eng.objects.get_connector_id_by_uri(root_uri)
        assert cid is not None
        rows = await eng.infra.meta.fetchall(
            "SELECT object_uri, priority FROM object_tasks WHERE connector_id=?", (cid,)
        )
        by_uri = {r["object_uri"].lstrip("/"): r["priority"] for r in rows}

        assert by_uri["archive/old.txt"] == 900  # user override
        assert by_uri["notes.md"] == -500  # user override, beats the built-in -350 bucket
        assert by_uri["README.md"] == -350  # no override -> falls back to file's own bucket
        assert by_uri["src/main.py"] == -220  # no override -> falls back to file's own bucket

        # end-to-end: the claim order (existing priority ASC, started_at ASC behavior)
        # actually reflects the override, not just the raw column value.
        claimed = await eng.objects.claim_tasks(cid, limit=10)
        order = [r["object_uri"].lstrip("/") for r in claimed]
        assert order.index("notes.md") < order.index("README.md")
        assert order.index("README.md") < order.index("src/main.py")
        assert order.index("src/main.py") < order.index("archive/old.txt")
    finally:
        await eng.infra.meta.close()
