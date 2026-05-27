"""Phase 13 — incremental re-index x recursive directory summary (matrix D14 / H7).

Changing one deep file must re-summarize only that file's ancestor directory chain; an
unrelated sibling subtree's directory_summary must stay untouched (same indexed_at).
Needs OPENAI_API_KEY. Lite.
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
    results.append(bool(cond)); print(f"  [{OK if cond else FAIL}] {name}"); return cond


async def _dir_indexed_at(eng, conn_uri, relpath):
    rows = await asyncio.to_thread(eng.milvus.get_chunks_by_object, "default", conn_uri, conn_uri + relpath)
    for r in rows:
        if r.get("chunk_kind") == "directory_summary":
            return r.get("indexed_at")
    return None


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic"); raise SystemExit(2)
    root = tempfile.mkdtemp(prefix="mfs_incr_")
    os.makedirs(f"{root}/a/deep", exist_ok=True); os.makedirs(f"{root}/b", exist_ok=True)
    open(f"{root}/a/deep/y.py", "w").write("def alpha():\n    return 'payment retry'\n")
    open(f"{root}/b/z.py", "w").write("def beta():\n    return 'unrelated sibling'\n")
    base = f"/tmp/mfs_incr_{os.getpid()}"; os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"; cfg.milvus.uri = base + "_v.db"; cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"; cfg.transformation_cache.db_path = base + "_t.db"
    cfg.summary.enabled = True
    eng = Engine(cfg)
    await eng.startup()
    conn_uri = f"file://local{root}"
    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")
        await eng.add(root)
        a0 = await _dir_indexed_at(eng, conn_uri, "/a")
        deep0 = await _dir_indexed_at(eng, conn_uri, "/a/deep")
        b0 = await _dir_indexed_at(eng, conn_uri, "/b")
        root0 = await _dir_indexed_at(eng, conn_uri, "/")
        check("initial: all four dir summaries exist", all(x is not None for x in (a0, deep0, b0, root0)))

        await asyncio.sleep(1.1)                       # ensure a distinct ms timestamp
        open(f"{root}/a/deep/y.py", "w").write("def alpha():\n    return 'refund workflow changed'\n")
        await eng.add(root, full=False)                # incremental

        a1 = await _dir_indexed_at(eng, conn_uri, "/a")
        deep1 = await _dir_indexed_at(eng, conn_uri, "/a/deep")
        b1 = await _dir_indexed_at(eng, conn_uri, "/b")
        root1 = await _dir_indexed_at(eng, conn_uri, "/")
        check("ancestor /a/deep re-summarized", deep1 is not None and deep1 > deep0)
        check("ancestor /a re-summarized", a1 is not None and a1 > a0)
        check("ancestor / re-summarized", root1 is not None and root1 > root0)
        check("sibling /b NOT re-summarized (indexed_at unchanged)", b1 == b0)
    finally:
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        await eng.shutdown(); shutil.rmtree(root, ignore_errors=True); os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'='*46}\n  incremental x dir-summary: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
