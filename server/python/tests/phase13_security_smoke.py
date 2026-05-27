"""Phase 13 — security (matrix I1 / I2 / I7), no external services.

  I1/I2 auth: /v1/* requires the bearer token (401 without/with wrong), /healthz is open.
  I7 redaction: _redact_config masks secret-looking keys and inline-credential URIs.
"""
import os
import shutil
import tempfile

from fastapi.testclient import TestClient

from mfs_server.api.app import create_app
from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append(bool(cond)); print(f"  [{OK if cond else FAIL}] {name}"); return cond


def main():
    base = f"/tmp/mfs_sec_{os.getpid()}"; os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"; cfg.milvus.uri = base + "_v.db"; cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"; cfg.transformation_cache.db_path = base + "_t.db"
    cfg.auth_token = "sekret-token"
    app = create_app(cfg)
    try:
        with TestClient(app) as client:
            r = client.get("/v1/status")
            check("I1 no token -> 401", r.status_code == 401)
            r = client.get("/v1/status", headers={"Authorization": "Bearer wrong"})
            check("I2 wrong token -> 401", r.status_code == 401)
            r = client.get("/v1/status", headers={"Authorization": "Bearer sekret-token"})
            check("valid token -> 200", r.status_code == 200)
            r = client.get("/healthz")
            check("I1 /healthz open (no token) -> 200", r.status_code == 200)

        # I7 — redaction of secret-looking keys + inline-credential connection strings
        cfg2 = load_server_config(apply_env=False)
        cfg2.metadata.path = base + "_m.db"; cfg2.milvus.uri = base + "_v.db"; cfg2.milvus.token = ""
        cfg2.object_store.root = base + "_c"; cfg2.transformation_cache.db_path = base + "_t.db"
        eng = Engine(cfg2)
        cfgblob = {
            "dsn": "postgresql://user:hunter2@db.internal:5432/app",
            "api_key": "sk-supersecret",
            "session_id": "abc123",
            "nested": {"token": "t0ken", "host": "ok-to-show.example.com"},
            "note": "connect via postgres://u:p@h/db inline",
            "indexable": True,
        }
        red = Engine._redact_config(cfgblob)
        check("I7 dsn redacted", "hunter2" not in str(red["dsn"]))
        check("I7 api_key redacted", "supersecret" not in str(red["api_key"]))
        check("I7 session_id redacted", red["session_id"] != "abc123")
        check("I7 nested token redacted", "t0ken" not in str(red["nested"]["token"]))
        check("I7 non-secret host preserved", red["nested"]["host"] == "ok-to-show.example.com")
        check("I7 inline-credential URI in free text redacted", "u:p@" not in str(red["note"]))
        check("I7 non-secret bool preserved", red["indexable"] is True)
    finally:
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'='*46}\n  security: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
