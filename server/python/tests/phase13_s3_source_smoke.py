"""Phase 13 — S3 SOURCE connector live e2e (matrix A: s3 as a source, not object store).

Seeds a MinIO bucket with files, indexes them through the s3:// connector (keys -> file
objects), searches, and reopens a file via cat (proving the AKID:SECRET credential_ref
survives the redacted-config reopen). Needs OPENAI_API_KEY + MinIO on :9100. Lite.
"""

import asyncio
import os

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []
EP = "http://127.0.0.1:9100"


def check(name, cond):
    results.append(bool(cond))
    print(f"  [{OK if cond else FAIL}] {name}")
    return cond


def _s3():
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=EP,
        aws_access_key_id="mfsadmin",
        aws_secret_access_key="mfsadmin123",
        region_name="us-east-1",
    )


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic")
        raise SystemExit(2)
    try:
        s3 = _s3()
        s3.list_buckets()
    except Exception as e:  # noqa: BLE001
        print(f"MinIO not reachable: {e}")
        raise SystemExit(2)
    bucket = f"mfs-src-{os.getpid()}"
    try:
        s3.create_bucket(Bucket=bucket)
    except Exception:
        pass
    s3.put_object(
        Bucket=bucket,
        Key="docs/readme.md",
        Body=b"# Platform\n\nThe ingestion pipeline overview and data flow.\n",
    )
    s3.put_object(
        Bucket=bucket,
        Key="src/app.py",
        Body=b"def charge(invoice):\n    # capture a payment for an invoice\n    return gateway.capture(invoice)\n",
    )

    base = f"/tmp/mfs_s3src_{os.getpid()}"
    os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"
    cfg.milvus.uri = base + "_v.db"
    cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"
    cfg.transformation_cache.db_path = base + "_t.db"
    cfg.summary.enabled = False
    eng = Engine(cfg)
    await eng.startup()
    os.environ["MFS_TEST_S3_CREDS"] = "mfsadmin:mfsadmin123"
    cfg_obj = {
        "bucket": bucket,
        "endpoint_url": EP,
        "region": "us-east-1",
        "credential_ref": "env:MFS_TEST_S3_CREDS",
    }
    conn_uri = "s3://src"
    try:
        eng.milvus.drop_collection("default")
        eng.milvus.ensure_collection("default")
        await eng.add("s3://src", config=cfg_obj)

        cid = (await eng.meta.fetchone("SELECT id FROM connectors WHERE type='s3'"))["id"]
        objs = await eng.meta.fetchall(
            "SELECT object_uri FROM objects WHERE connector_id=?", (cid,)
        )
        uris = {o["object_uri"] for o in objs}
        check(
            "s3 source indexed both keys",
            any("readme.md" in u for u in uris) and any("app.py" in u for u in uris),
        )

        r1 = await eng.search(
            "ingestion pipeline data flow", connector_uri=conn_uri, mode="hybrid", top_k=5
        )
        check("s3 source: md searchable", any("readme.md" in (e["source"] or "") for e in r1))
        r2 = await eng.search(
            "capture a payment for an invoice", connector_uri=conn_uri, mode="hybrid", top_k=5
        )
        check("s3 source: code searchable", any("app.py" in (e["source"] or "") for e in r2))

        # cat reopens the connector -> exercises the credential_ref (redacted config) path
        src = next((e["source"] for e in r1 if "readme.md" in (e["source"] or "")), None)
        if src:
            txt = await eng.cat(src)
            check("s3 source: cat reopen works (credential_ref)", "ingestion pipeline" in txt)
    finally:
        try:
            eng.milvus.drop_collection("default")
        except Exception:
            pass
        await eng.shutdown()
        try:
            for o in s3.list_objects_v2(Bucket=bucket).get("Contents", []):
                s3.delete_object(Bucket=bucket, Key=o["Key"])
            s3.delete_bucket(Bucket=bucket)
        except Exception:
            pass
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'=' * 46}\n  s3 source connector: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
