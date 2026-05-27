"""Phase 4 HTTP API smoke — needs OPENAI_API_KEY (bash -ic).

Drives the FastAPI /v1 endpoints via TestClient (triggers lifespan startup): add ->
search (hybrid) -> grep -> ls -> cat -> status -> job. Lite backend, temp paths.
"""
import os
import shutil
import tempfile
import time

from fastapi.testclient import TestClient

from mfs_server.api.app import create_app
from mfs_server.config import load_server_config

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append((name, cond))
    print(f"  [{OK if cond else FAIL}] {name}")
    return cond


def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic")
        raise SystemExit(2)
    root = tempfile.mkdtemp(prefix="mfs_p4api_repo_")
    open(f"{root}/auth.md", "w").write("# Session storage\n\nUser sessions live in Redis with a TTL.\n")
    open(f"{root}/README.md", "w").write("# Demo\n\nUnrelated banana content.\n")

    base = f"/tmp/mfs_p4api_{os.getpid()}"
    os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_meta.db"
    cfg.milvus.uri = base + "_milvus.db"
    cfg.milvus.token = ""
    cfg.object_store.root = base + "_cache"
    cfg.transformation_cache.db_path = base + "_tx.db"

    app = create_app(cfg)
    try:
        with TestClient(app) as client:
            r = client.get("/v1/server/info")
            check("GET /v1/server/info 200", r.status_code == 200 and r.json()["version"] == "0.4.0")

            # add is async: returns a job_id immediately; the in-process worker (sqlite)
            # drains it in the background, so poll the job to completion before searching.
            r = client.post("/v1/add", json={"target": root})
            check("POST /v1/add returns job_id", r.status_code == 200 and "job_id" in r.json())
            job_id = r.json()["job_id"]

            status = None
            for _ in range(120):
                jr = client.get("/v1/jobs/" + job_id).json()
                status = jr["status"]
                if status in ("succeeded", "failed", "cancelled"):
                    break
                time.sleep(0.5)
            check("GET /v1/jobs/{id} succeeded (async drain)", status == "succeeded")

            r = client.get("/v1/search", params={"q": "user sessions storage", "path": root, "mode": "hybrid", "top_k": 5})
            res = r.json()["results"]
            check("GET /v1/search returns results", r.status_code == 200 and len(res) > 0)
            check("search top is session-related", any("auth.md" in (x["source"] or "") for x in res[:2]))

            r = client.get("/v1/grep", params={"pattern": "Session", "path": root})
            check("GET /v1/grep returns hits", r.status_code == 200 and len(r.json()["results"]) > 0)

            r = client.get("/v1/ls", params={"path": root})
            names = {e["name"] for e in r.json()["entries"]}
            check("GET /v1/ls lists files", r.status_code == 200 and {"auth.md", "README.md"} <= names)

            r = client.get("/v1/cat", params={"path": f"{root}/auth.md"})
            check("GET /v1/cat returns content", r.status_code == 200 and "Session storage" in r.json()["content"])

            r = client.get("/v1/cat", params={"path": f"{root}/auth.md", "range": "0:1"})
            check("GET /v1/cat --range", r.status_code == 200 and "Session storage" in r.json()["content"])

            r = client.get("/v1/status")
            check("GET /v1/status", r.status_code == 200 and len(r.json()["connectors"]) >= 1)
    finally:
        shutil.rmtree(root, ignore_errors=True)
        os.system(f"rm -rf '{base}'*")

    passed = sum(1 for _, c in results if c)
    total = len(results)
    print(f"\n{'='*40}\nPhase 4 HTTP API: {passed}/{total} checks passed")
    if passed != total:
        print("FAILED:", [n for n, c in results if not c])
        raise SystemExit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
