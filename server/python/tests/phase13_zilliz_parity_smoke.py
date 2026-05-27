"""Phase 13 — Milvus Zilliz Cloud parity (matrix B2).

The full add -> search -> directory-summary -> remove flow must behave on Zilliz Cloud
exactly as on Lite. Isolated in its own per-namespace collection (mfs_chunks__<ns>__...)
so it never touches a shared collection, and dropped on exit. Needs OPENAI_API_KEY and
ZILLIZ_URI / ZILLIZ_API_KEY.
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
    uri = os.environ.get("ZILLIZ_URI"); tok = os.environ.get("ZILLIZ_API_KEY")
    if not os.environ.get("OPENAI_API_KEY") or not uri or not tok:
        print("need OPENAI_API_KEY + ZILLIZ_URI + ZILLIZ_API_KEY — run via bash -ic"); raise SystemExit(2)
    ns = f"mfstest{os.getpid()}"
    root = tempfile.mkdtemp(prefix="mfs_zp_")
    os.makedirs(f"{root}/svc", exist_ok=True)
    open(f"{root}/README.md", "w").write("# Orders\n\nHandles checkout, payment capture and refunds.\n")
    open(f"{root}/svc/pay.py", "w").write("def capture(order):\n    return gateway.capture(order)\n")
    base = f"/tmp/mfs_zp_{os.getpid()}"; os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.namespace = ns
    cfg.metadata.path = base + "_m.db"
    cfg.milvus.uri = uri; cfg.milvus.token = tok; cfg.milvus.collection_strategy = "per_namespace"
    cfg.object_store.root = base + "_c"; cfg.transformation_cache.db_path = base + "_t.db"
    cfg.summary.enabled = True
    eng = Engine(cfg)
    await eng.startup()
    conn_uri = f"file://local{root}"
    try:
        coll = eng.milvus.resolve_collection(ns)
        check("per-namespace collection name carries ns", ns in coll)
        await eng.add(root)

        n_total = await asyncio.to_thread(eng.milvus.count, ns)
        n_dir = await asyncio.to_thread(eng.milvus.count, ns, 'chunk_kind == "directory_summary"')
        check("Zilliz: chunks written", n_total > 0)
        check("Zilliz: directory summaries present (root + /svc = 2)", n_dir == 2)

        res = await eng.search("payment capture refund", connector_uri=conn_uri, mode="hybrid", top_k=5)
        check("Zilliz: hybrid search returns hits", len(res) > 0)
        kinds = {e.get("metadata", {}).get("chunk_kind") for e in res}
        check("Zilliz: no per-file summary chunk_kind", "summary" not in kinds)

        removed = await eng.remove_connector(root)
        check("Zilliz: remove_connector succeeds", removed is True)
        n_after = await asyncio.to_thread(eng.milvus.count, ns)
        check("Zilliz: chunks purged after remove", n_after == 0)
    finally:
        try: eng.milvus.drop_collection(ns)
        except Exception: pass
        await eng.shutdown(); shutil.rmtree(root, ignore_errors=True); os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'='*46}\n  Zilliz parity: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
