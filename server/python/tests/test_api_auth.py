from __future__ import annotations

from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from mfs_server.api.app import _auth_failure
from mfs_server.api.app import create_app
from mfs_server.config import ServerConfig


def _headers(*values: str) -> Headers:
    return Headers(raw=[(b"authorization", value.encode("ascii")) for value in values])


def test_auth_accepts_case_insensitive_header_and_scheme() -> None:
    assert _auth_failure(_headers("bearer expected"), "expected") is None


def test_auth_rejects_duplicate_authorization_headers() -> None:
    failure = _auth_failure(_headers("Bearer expected", "Bearer wrong"), "expected")

    assert failure is not None
    status, body = failure
    assert status == 400
    assert body["code"] == "bad_request"
    assert body["detail"] == "duplicate Authorization header"


def test_auth_rejects_empty_and_whitespace_tokens() -> None:
    for value in ("Bearer", "Bearer ", "Bearer  expected", "Bearer expected "):
        failure = _auth_failure(_headers(value), "expected")

        assert failure is not None
        status, body = failure
        assert status == 401
        assert body["code"] == "unauthorized"


def test_openapi_documents_bearer_auth_and_error_envelope(tmp_path) -> None:
    cfg = ServerConfig(home=str(tmp_path), auth_token="expected").resolve_defaults()
    app = create_app(cfg)
    client = TestClient(app)

    response = client.get("/openapi.json", headers={"Authorization": "Bearer expected"})

    assert response.status_code == 200
    spec = response.json()
    assert spec["components"]["securitySchemes"]["BearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "opaque",
    }
    assert spec["components"]["schemas"]["ErrorResponse"]["type"] == "object"
    assert "HTTPValidationError" not in spec["components"]["schemas"]
    assert "ValidationError" not in spec["components"]["schemas"]
    assert spec["paths"]["/healthz"]["get"]["security"] == []

    for path in ("/v1/status", "/v1/jobs", "/v1/search", "/v1/grep", "/v1/ls", "/v1/cat"):
        operation = spec["paths"][path]["get"]
        assert operation["security"] == [{"BearerAuth": []}]
        assert operation["responses"]["401"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }
        assert operation["responses"]["405"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }
        assert operation["responses"]["422"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }


def test_validation_errors_use_documented_envelope(tmp_path) -> None:
    cfg = ServerConfig(home=str(tmp_path), auth_token="expected").resolve_defaults()
    app = create_app(cfg)
    client = TestClient(app)

    response = client.get("/v1/search", headers={"Authorization": "Bearer expected"})

    assert response.status_code == 422
    assert response.json() == {
        "code": "validation_error",
        "detail": "query.q: Field required",
        "suggestions": ["fix request shape"],
    }


def test_search_rejects_invalid_mode(tmp_path) -> None:
    cfg = ServerConfig(home=str(tmp_path), auth_token="expected").resolve_defaults()
    app = create_app(cfg)
    client = TestClient(app)

    response = client.get(
        "/v1/search?q=needle&mode=definitely_bad",
        headers={"Authorization": "Bearer expected"},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert "query.mode" in body["detail"]
    assert body["suggestions"] == ["fix request shape"]


def test_search_rejects_unknown_query_params(tmp_path) -> None:
    cfg = ServerConfig(home=str(tmp_path), auth_token="expected").resolve_defaults()
    app = create_app(cfg)
    client = TestClient(app)

    response = client.get(
        "/v1/search?q=needle&offset=1",
        headers={"Authorization": "Bearer expected"},
    )

    assert response.status_code == 422
    assert response.json() == {
        "code": "validation_error",
        "detail": "unknown query parameter(s): offset",
        "suggestions": ["fix request shape"],
    }


def test_framework_http_errors_use_documented_envelope(tmp_path) -> None:
    cfg = ServerConfig(home=str(tmp_path), auth_token="expected").resolve_defaults()
    app = create_app(cfg)
    client = TestClient(app)

    missing = client.get("/v1/does-not-exist", headers={"Authorization": "Bearer expected"})
    wrong_method = client.post("/v1/status", headers={"Authorization": "Bearer expected"})

    assert missing.status_code == 404
    assert missing.json() == {
        "code": "not_found",
        "detail": "Not Found",
        "suggestions": ["check the URI"],
    }
    assert wrong_method.status_code == 405
    assert wrong_method.headers["allow"] == "GET"
    assert wrong_method.json() == {
        "code": "method_not_allowed",
        "detail": "Method Not Allowed",
        "suggestions": [],
    }
