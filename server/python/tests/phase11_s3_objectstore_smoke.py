"""Phase 11 — S3/MinIO object store backend (boto3). Runs against the local MinIO
container (endpoint 127.0.0.1:9100, no AWS account). Verifies artifact put/get via
the engine pipeline: an HTML doc indexes -> converted_md artifact lands in the S3
bucket and `cat` reads it back from S3. Needs MinIO container + OPENAI_API_KEY.
"""
import asyncio
import os

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine
from mfs_server.storage.object_store import S3ObjectStore

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append(bool(cond))
    print(f"  [{OK if cond else FAIL}] {name}")
    return cond


def _cfg_s3(cfg):
    oc = cfg.object_store
    oc.backend = "s3"; oc.bucket = "mfs-test"; oc.prefix = "mfs"
    oc.endpoint_url = "http://127.0.0.1:9100"; oc.region = "us-east-1"
    oc.access_key_id = "mfsadmin"; oc.secret_access_key = "mfsadmin123"


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic"); raise SystemExit(2)
    base = f"/tmp/mfs_s3_{os.getpid()}"
    os.system(f"rm -rf '{base}'*")
    repo = base + "_repo"; os.makedirs(repo, exist_ok=True)
    # an .html doc -> converter produces a converted_md artifact (stored in S3)
    open(repo + "/page.html", "w").write(
        "<html><body><h1>SSO</h1><p>Single sign-on via SAML token service.</p></body></html>")

    cfg = load_server_config(apply_env=False)
    cfg.milvus.uri = base + "_milvus.db"; cfg.milvus.token = ""
    cfg.metadata.path = base + "_meta.db"; cfg.transformation_cache.db_path = base + "_tx.db"
    cfg.object_store.root = base + "_staging"
    _cfg_s3(cfg)

    eng = Engine(cfg)
    check("engine uses S3ObjectStore", isinstance(eng.object_store, S3ObjectStore))
    await eng.startup()
    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")
        await eng.add(repo)

        # artifact landed in the S3 bucket
        curi = None
        conn = await eng.meta.fetchone("SELECT root_uri FROM connectors WHERE type='file'")
        curi = conn["root_uri"]
        full_uri = curi + "/page.html"
        art = await asyncio.to_thread(eng.object_store.get_artifact, eng.ns, full_uri, "converted_md")
        check("converted_md artifact retrievable from S3", art is not None and b"SSO" in art)

        # list bucket directly to prove bytes are really in MinIO
        s3 = eng.object_store._s3
        listing = s3.list_objects_v2(Bucket="mfs-test", Prefix="mfs/artifacts/")
        keys = [o["Key"] for o in listing.get("Contents", [])]
        check("S3 bucket has artifact object(s)", any("converted_md" in k for k in keys))

        # cat reads markdown back from S3
        out = await eng.cat(repo + "/page.html")
        check("cat returns converted markdown from S3", "SSO" in out)

        # search works (vectors in Milvus, artifact bytes in S3)
        res = await eng.search("single sign-on SAML", connector_uri=curi, mode="hybrid", top_k=2)
        check("search hits the html page", res and res[0]["source"].endswith("page.html"))
    finally:
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        # clean the test bucket
        try:
            s3 = eng.object_store._s3
            objs = s3.list_objects_v2(Bucket="mfs-test").get("Contents", [])
            for o in objs:
                s3.delete_object(Bucket="mfs-test", Key=o["Key"])
        except Exception: pass
        await eng.shutdown()
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'='*46}\n  S3/MinIO object store: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
