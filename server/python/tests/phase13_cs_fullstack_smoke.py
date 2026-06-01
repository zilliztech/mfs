"""Phase 13 — full CS-shaped stack in one flow (matrix B7).

Milvus Zilliz Cloud + Postgres metadata + S3/MinIO object store + recursive directory
summaries, exercised end to end: add -> chunks in Zilliz, metadata rows in Postgres,
converted artifact bytes in S3, search hits, then remove cleans all of it up. Isolated in
its own namespace / per-namespace collection. Needs OPENAI_API_KEY + ZILLIZ_URI/_API_KEY
and the local Postgres + MinIO (:9100) the other phase tests use.
"""

import asyncio
import os
import shutil
import tempfile

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine
from mfs_server.storage.object_store import S3ObjectStore

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []
PG_DSN = "postgresql://zhangchen@/mfstest?host=/var/run/postgresql"


def check(name, cond):
    results.append(bool(cond))
    print(f"  [{OK if cond else FAIL}] {name}")
    return cond


async def main():
    uri = os.environ.get("ZILLIZ_URI")
    tok = os.environ.get("ZILLIZ_API_KEY")
    if not os.environ.get("OPENAI_API_KEY") or not uri or not tok:
        print("need OPENAI_API_KEY + ZILLIZ_URI + ZILLIZ_API_KEY — run via bash -ic")
        raise SystemExit(2)
    ns = f"cs{os.getpid()}"
    root = tempfile.mkdtemp(prefix="mfs_cs_")
    os.makedirs(f"{root}/docs", exist_ok=True)
    open(f"{root}/README.md", "w").write("# Platform\n\nMulti-source retrieval control plane.\n")
    open(f"{root}/docs/guide.html", "w").write(
        "<html><body><h1>Deploy Guide</h1><p>Run the worker replicas behind the API.</p></body></html>"
    )
    base = f"/tmp/mfs_cs_{os.getpid()}"
    os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.namespace = ns
    cfg.milvus.uri = uri
    cfg.milvus.token = tok
    cfg.milvus.collection_strategy = "per_namespace"
    cfg.metadata.backend = "postgres"
    cfg.metadata.dsn = PG_DSN
    cfg.transformation_cache.backend = "postgres"
    cfg.transformation_cache.dsn = PG_DSN
    cfg.object_store.backend = "s3"
    cfg.object_store.bucket = "mfs-test"
    cfg.object_store.prefix = ns
    cfg.object_store.endpoint_url = "http://127.0.0.1:9100"
    cfg.object_store.region = "us-east-1"
    cfg.object_store.access_key_id = "mfsadmin"
    cfg.object_store.secret_access_key = "mfsadmin123"
    cfg.object_store.root = base + "_staging"
    cfg.summary.enabled = True
    eng = Engine(cfg)
    await eng.startup()
    conn_uri = f"file://local{root}"
    try:
        check("object store is S3", isinstance(eng.object_store, S3ObjectStore))
        check("metadata backend is postgres", eng.meta.backend == "postgres")
        await eng.add(root)

        n_total = await asyncio.to_thread(eng.milvus.count, ns)
        n_dir = await asyncio.to_thread(eng.milvus.count, ns, 'chunk_kind == "directory_summary"')
        check("Zilliz: chunks written", n_total > 0)
        check("Zilliz: directory summaries present (root + /docs)", n_dir == 2)

        conn = await eng.meta.fetchone("SELECT id FROM connectors WHERE root_uri=?", (conn_uri,))
        check("Postgres: connector row present", conn is not None)

        art = await asyncio.to_thread(
            eng.object_store.get_artifact, ns, conn_uri + "/docs/guide.html", "converted_md"
        )
        check("S3: converted_md artifact retrievable", art is not None and b"Deploy" in art)

        res = await eng.search(
            "worker replicas behind the api", connector_uri=conn_uri, mode="hybrid", top_k=5
        )
        check("search returns hits across the CS stack", len(res) > 0)

        removed = await eng.remove_connector(root)
        check("remove cleans up", removed is True)
        check("Zilliz: purged after remove", await asyncio.to_thread(eng.milvus.count, ns) == 0)
        left = await eng.meta.fetchone("SELECT id FROM connectors WHERE root_uri=?", (conn_uri,))
        check("Postgres: connector row removed", left is None)
    finally:
        try:
            eng.milvus.drop_collection(ns)
        except Exception:
            pass
        await eng.shutdown()
        shutil.rmtree(root, ignore_errors=True)
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'=' * 46}\n  CS full stack (Zilliz+PG+S3): {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
