"""Phase 13 — embedding-dim change isolates collections (matrix B8 / K2). Lite.

The collection name embeds the schema version and embedding dim. Switching the embedding
dimension must target a fresh collection, leaving the old one (built for a different model)
untouched — never silently writing mismatched-dim vectors into it. Needs OPENAI_API_KEY.
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


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic"); raise SystemExit(2)
    root = tempfile.mkdtemp(prefix="mfs_dim_")
    open(f"{root}/n.md", "w").write("# Indexing\n\nDense vectors live in an HNSW index.\n")
    base = f"/tmp/mfs_dim_{os.getpid()}"; os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"; cfg.milvus.uri = base + "_v.db"; cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"; cfg.transformation_cache.db_path = base + "_t.db"
    cfg.summary.enabled = False
    eng = Engine(cfg)
    await eng.startup()
    try:
        coll_1536 = eng.milvus.resolve_collection("default")
        check("collection name carries dim", coll_1536.endswith("_d1536") and "v" in coll_1536)
        await eng.add(root)
        n0 = await asyncio.to_thread(eng.milvus.count, "default")
        check("indexed at dim 1536", n0 > 0)

        # simulate switching embedding dimension
        eng.milvus.dim = 512
        coll_512 = eng.milvus.resolve_collection("default")
        check("dim switch -> different collection name", coll_512 != coll_1536 and coll_512.endswith("_d512"))
        check("old collection still exists", eng.milvus.client.has_collection(coll_1536))
        check("new-dim collection does not exist yet", not eng.milvus.client.has_collection(coll_512))
        # old collection untouched (still has the original vectors)
        old_cnt = eng.milvus.client.query(collection_name=coll_1536, filter="chunk_id != ''",
                                          output_fields=["count(*)"], consistency_level="Strong")[0]["count(*)"]
        check("old-dim collection data intact", old_cnt == n0)
    finally:
        for c in (eng.milvus.resolve_collection("default"),):
            pass
        try:
            eng.milvus.dim = 1536; eng.milvus.drop_collection("default")
        except Exception:
            pass
        await eng.shutdown(); shutil.rmtree(root, ignore_errors=True); os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'='*46}\n  dim-switch collection isolation: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
