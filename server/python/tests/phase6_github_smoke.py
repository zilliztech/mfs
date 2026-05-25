"""Phase 6 github connector smoke — public repo tree -> index -> search.
Needs GITHUB_TOKEN + OPENAI_API_KEY + network (bash -ic). octocat/Spoon-Knife
(README.md + index.html + styles.css). Lite backend.
"""
import asyncio
import os

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append((name, cond))
    print(f"  [{OK if cond else FAIL}] {name}")
    return cond


async def main():
    if not (os.environ.get("OPENAI_API_KEY") and os.environ.get("GITHUB_TOKEN")):
        print("need OPENAI_API_KEY + GITHUB_TOKEN — run via bash -ic")
        raise SystemExit(2)
    base = f"/tmp/mfs_p6gh_{os.getpid()}"
    os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_meta.db"
    cfg.milvus.uri = base + "_milvus.db"
    cfg.milvus.token = ""
    cfg.object_store.root = base + "_cache"
    cfg.transformation_cache.db_path = base + "_tx.db"
    eng = Engine(cfg)
    await eng.startup()
    try:
        eng.milvus.drop_collection(cfg.namespace)
        eng.milvus.ensure_collection(cfg.namespace)

        await eng.add("github://spoon", config={"repo": "octocat/Spoon-Knife"})
        conn = await eng.meta.fetchone("SELECT id FROM connectors WHERE type='github'")
        check("github connector registered", conn is not None)
        objs = {o["object_uri"]: o for o in await eng.meta.fetchall(
            "SELECT object_uri, chunk_count, search_status FROM objects WHERE connector_id=?", (conn["id"],))}
        check("README.md indexed", objs.get("/README.md", {}).get("chunk_count", 0) > 0
              and objs["/README.md"]["search_status"] == "indexed")
        check("index.html converted+indexed", objs.get("/index.html", {}).get("chunk_count", 0) > 0)

        res = await eng.search("example repository for forking practice", connector_uri="github://spoon",
                               mode="hybrid", top_k=3)
        check("search returns repo files", len(res) > 0 and any("README" in (e["source"] or "") for e in res[:3]))

        # re-add: blob shas unchanged -> 0 tasks
        job2 = await eng.add("github://spoon")
        t2 = await eng.meta.fetchall("SELECT id FROM object_tasks WHERE connector_job_id=?", (job2,))
        check("re-add idempotent (blob sha unchanged): 0 tasks", len(t2) == 0)
    finally:
        try:
            eng.milvus.drop_collection(cfg.namespace)
        except Exception:
            pass
        await eng.shutdown()
        os.system(f"rm -rf '{base}'*")

    passed = sum(1 for _, c in results if c)
    total = len(results)
    print(f"\n{'='*40}\nPhase 6 github: {passed}/{total} checks passed")
    if passed != total:
        print("FAILED:", [n for n, c in results if not c])
        raise SystemExit(1)
    print("ALL PASS")


if __name__ == "__main__":
    asyncio.run(main())
