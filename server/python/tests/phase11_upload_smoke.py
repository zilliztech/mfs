"""Phase 11 — CS upload flow (client/server don't share fs). Builds a tar.gz in
memory, uploads via the HTTP API, and verifies the server stages + indexes it and
search works. Also checks zip-slip (path traversal) is rejected. Needs OPENAI_API_KEY
(bash -ic). Milvus Lite.
"""
import asyncio
import io
import os
import tarfile

from fastapi.testclient import TestClient

from mfs_server.api.app import create_app
from mfs_server.config import load_server_config

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append(bool(cond))
    print(f"  [{OK if cond else FAIL}] {name}")
    return cond


def _make_tar(files: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for path, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(name=path)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic"); raise SystemExit(2)
    base = f"/tmp/mfs_up_{os.getpid()}"
    os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_meta.db"; cfg.milvus.uri = base + "_milvus.db"; cfg.milvus.token = ""
    cfg.object_store.root = base + "_store"; cfg.transformation_cache.db_path = base + "_tx.db"

    app = create_app(cfg)
    with TestClient(app) as client:
        tar = _make_tar({
            "docs/auth.md": "# Auth\nSingle sign-on via SAML; sessions validate against the token service.\n",
            "docs/notes/cache.md": "# Cache\nResults memoized in a content-addressable transformation cache.\n",
        })
        r = client.post("/v1/upload?name=acme-bundle", content=tar,
                        headers={"content-type": "application/gzip"})
        check("upload returns 200 + job_id", r.status_code == 200 and "job_id" in r.json())

        # search across the staged + indexed upload
        sr = client.get("/v1/search", params={"q": "single sign-on token service", "top_k": 3})
        results_json = sr.json().get("results", [])
        check("search finds the uploaded auth.md",
              any(e["source"].endswith("auth.md") for e in results_json))
        # nested path preserved -> reads back via cat
        top = next((e for e in results_json if e["source"].endswith("auth.md")), None)
        if top:
            cat = client.get("/v1/cat", params={"path": top["source"].replace("file://local", "")})
            check("cat reads staged file content", "Single sign-on" in cat.json().get("content", ""))
        else:
            check("cat reads staged file content", False)

        # zip-slip: a member escaping the staging dir must be rejected
        evil = _make_tar({"../../etc/evil.md": "pwned\n"})
        er = client.post("/v1/upload?name=evil", content=evil,
                         headers={"content-type": "application/gzip"})
        check("zip-slip upload rejected (400)", er.status_code == 400)

    passed = sum(results)
    print(f"\n{'='*46}\n  CS upload flow: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
