from __future__ import annotations

from fastapi.testclient import TestClient

from mfs_server.api.app import create_app
from mfs_server.config import ServerConfig

# Regression guard for the read-only server endpoints that only touch the metadata
# store. These go through `eng().infra.meta`, and an earlier refactor that dropped the
# `Engine.meta` forwarding property (InfraStack) left the call sites reading the removed
# attribute — every request 500'd with `'Engine' object has no attribute 'meta'`. The
# endpoints had no coverage, so the break shipped silently. Driving them through a real
# TestClient lifespan (which builds the Engine against the tmp SQLite + Milvus Lite)
# keeps that whole class of "endpoint wired to a stale handle" regression caught.


def _client(tmp_path) -> TestClient:
    cfg = ServerConfig(home=str(tmp_path), auth_token="expected").resolve_defaults()
    return TestClient(create_app(cfg))


def test_status_endpoint_reachable_on_empty_store(tmp_path) -> None:
    with _client(tmp_path) as client:
        client.headers["Authorization"] = "Bearer expected"
        response = client.get("/v1/status")

    assert response.status_code == 200
    assert response.json() == {"connectors": [], "jobs": {}}


def test_jobs_list_endpoint_reachable_on_empty_store(tmp_path) -> None:
    with _client(tmp_path) as client:
        client.headers["Authorization"] = "Bearer expected"
        response = client.get("/v1/jobs")

    assert response.status_code == 200
    assert response.json() == []


def test_job_lookup_missing_id_returns_404_not_500(tmp_path) -> None:
    with _client(tmp_path) as client:
        client.headers["Authorization"] = "Bearer expected"
        response = client.get("/v1/jobs/does-not-exist")

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"
