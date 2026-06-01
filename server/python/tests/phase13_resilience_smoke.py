"""Phase 13 — resilience to a missing collection (matrix C8 / J3).

After indexing, the Milvus collection is dropped out-of-band (simulating a serverless
reset / external drop). search must return [] instead of throwing, count must read 0, and
remove_connector must still succeed (not wedge in 'removing'). Needs OPENAI_API_KEY. Lite.
"""

import asyncio
import os
import shutil
import tempfile

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append(bool(cond))
    print(f"  [{OK if cond else FAIL}] {name}")
    return cond


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic")
        raise SystemExit(2)
    root = tempfile.mkdtemp(prefix="mfs_res_")
    open(f"{root}/notes.md", "w").write(
        "# Caching\n\nLRU eviction keeps the artifact store under budget.\n"
    )
    base = f"/tmp/mfs_res_{os.getpid()}"
    os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"
    cfg.milvus.uri = base + "_v.db"
    cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"
    cfg.transformation_cache.db_path = base + "_t.db"
    eng = Engine(cfg)
    await eng.startup()
    try:
        eng.milvus.drop_collection("default")
        eng.milvus.ensure_collection("default")
        await eng.add(root)
        res = await eng.search("artifact cache eviction", mode="hybrid", top_k=3)
        check("baseline: search returns hits", len(res) > 0)

        # simulate an external drop of the collection
        await asyncio.to_thread(eng.milvus.drop_collection, "default")

        res2 = await eng.search("artifact cache eviction", mode="hybrid", top_k=3)
        check("search on missing collection returns [] (no throw)", res2 == [])
        cnt = await asyncio.to_thread(eng.milvus.count, "default")
        check("count on missing collection returns 0", cnt == 0)
        chunks = await asyncio.to_thread(
            eng.milvus.get_chunks_by_object,
            "default",
            f"file://local{root}",
            f"file://local{root}/notes.md",
        )
        check("get_chunks_by_object on missing collection returns []", chunks == [])

        removed = await eng.remove_connector(root)
        check("remove_connector succeeds despite missing collection (no wedge)", removed is True)
        gone = await eng.meta.fetchone(
            "SELECT 1 FROM connectors WHERE root_uri=?", (f"file://local{root}",)
        )
        check("connector row fully removed", gone is None)
    finally:
        try:
            eng.milvus.drop_collection("default")
        except Exception:
            pass
        await eng.shutdown()
        shutil.rmtree(root, ignore_errors=True)
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'=' * 46}\n  resilience (missing collection): {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
