from __future__ import annotations

from starlette.datastructures import Headers

from mfs_server.api.app import _auth_failure


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
