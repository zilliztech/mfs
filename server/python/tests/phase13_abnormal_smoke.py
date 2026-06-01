"""Phase 13 — abnormal / error-path usage: every bad input fails cleanly (clear error or
empty result), never an unhandled crash or corrupt state. Needs OPENAI_API_KEY. Lite.
"""

import asyncio
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
    results.append(bool(cond))
    print(f"  [{OK if cond else FAIL}] {name}")
    return cond


async def expect_raise(
    name, coro, *, clean_types=(ValueError, FileNotFoundError, KeyError, IsADirectoryError)
):
    try:
        await coro
        check(name + " (raised)", False)
    except clean_types:
        check(name, True)
    except Exception as e:  # noqa: BLE001 - an unexpected exception type is a poor error path
        check(f"{name} [unexpected {type(e).__name__}: {str(e)[:60]}]", False)


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic")
        raise SystemExit(2)
    root = tempfile.mkdtemp(prefix="mfs_abn_")
    open(f"{root}/a.md", "w").write("# A\n\nordinary document content for retrieval.\n")
    base = f"/tmp/mfs_abn_{os.getpid()}"
    os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"
    cfg.milvus.uri = base + "_v.db"
    cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"
    cfg.transformation_cache.db_path = base + "_t.db"
    cfg.auth_token = "tok"
    cfg.summary.enabled = False
    eng = Engine(cfg)
    await eng.startup()
    cu = f"file://local{root}"
    try:
        eng.milvus.drop_collection("default")
        eng.milvus.ensure_collection("default")
        await eng.add(root)

        # 1. cat a path that doesn't exist
        await expect_raise("cat nonexistent file -> clean error", eng.cat(f"{root}/nope.md"))
        # 2. cat --locator on a non-structured (document) object
        await expect_raise(
            "cat --locator on a document -> clean error", eng.cat(f"{root}/a.md", locator={"id": 1})
        )
        # 3. grep a path outside any connector
        await expect_raise(
            "grep outside any connector -> clean error", eng.grep("x", "/no/such/place")
        )
        # 4. remove a connector that doesn't exist -> falsy, no crash
        try:
            removed = await eng.remove_connector("/no/such/connector/path")
            check("remove nonexistent connector -> falsy (no crash)", not removed)
        except ValueError:
            check("remove nonexistent connector -> clean error", True)
        # 5. search with a bogus connector scope -> empty, no crash
        r = await eng.search(
            "anything", connector_uri="file://local/nowhere", mode="hybrid", top_k=5
        )
        check("search bogus scope -> [] (no crash)", r == [])
        # 6. idempotent re-add: same path twice -> still exactly one connector
        await eng.add(root)
        n = await eng.meta.fetchone("SELECT count(*) AS n FROM connectors")
        check("re-add same path -> still one connector (idempotent)", n["n"] == 1)
        # 7. cat --range with start>end -> empty, no crash
        out = await eng.cat(f"{root}/a.md", range=(5, 2))
        check(
            "cat --range start>end -> empty, no crash", isinstance(out, str) and out.strip() == ""
        )
        # 8. add postgres with a missing credential_ref -> clear error
        await expect_raise(
            "add with missing env credential_ref -> clear error",
            eng.add(
                "postgres://bad",
                config={"credential_ref": "env:MFS_NOPE_MISSING", "schemas": ["public"]},
            ),
        )

        # 9. HTTP: job + auth abnormal paths
        app = create_app(cfg)
        with TestClient(app) as client:
            h = {"Authorization": "Bearer tok"}
            check(
                "GET /v1/jobs/<bad> -> 404",
                client.get("/v1/jobs/deadbeef", headers=h).status_code == 404,
            )
            cr = client.post("/v1/jobs/deadbeef/cancel", headers=h)
            check(
                "cancel /v1/jobs/<bad> -> no 5xx, not cancelled",
                cr.status_code < 500
                and (cr.status_code >= 400 or cr.json().get("cancelled") is False),
            )
            check(
                "GET /v1/cat missing path param -> 422",
                client.get("/v1/cat", headers=h).status_code == 422,
            )
            check(
                "GET /v1/search missing q -> 422",
                client.get("/v1/search", headers=h).status_code == 422,
            )
    finally:
        try:
            eng.milvus.drop_collection("default")
        except Exception:
            pass
        await eng.shutdown()
        shutil.rmtree(root, ignore_errors=True)
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'=' * 46}\n  abnormal usage: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
