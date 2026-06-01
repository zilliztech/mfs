"""Phase 13 — multiple connectors: coexistence, remove isolation, single-flight
(matrix G1 / G2 / F8). Needs OPENAI_API_KEY. Lite.
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
    da = tempfile.mkdtemp(prefix="mfs_mc_a_")
    db = tempfile.mkdtemp(prefix="mfs_mc_b_")
    open(f"{da}/a.md", "w").write("# Alpha\n\nDistributed consensus via raft leader election.\n")
    open(f"{db}/b.md", "w").write("# Beta\n\nColumnar storage with zonemap pruning.\n")
    base = f"/tmp/mfs_mc_{os.getpid()}"
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
    try:
        eng.milvus.drop_collection("default")
        eng.milvus.ensure_collection("default")
        await eng.add(da)
        await eng.add(db)

        conns = await eng.meta.fetchall("SELECT root_uri FROM connectors")
        check("G1: two connectors registered", len(conns) == 2)
        allres = await eng.search("storage consensus", mode="hybrid", top_k=10)  # --all
        srcs = " ".join(e["source"] or "" for e in allres)
        check("G1: --all search spans both connectors", "a.md" in srcs and "b.md" in srcs)

        # F8 — single-flight: a second in-flight sync for the same connector is rejected
        cid = (
            await eng.meta.fetchone(
                "SELECT id FROM connectors WHERE root_uri=?", (f"file://local{da}",)
            )
        )["id"]
        j1 = await eng._open_sync_job(cid, process=True)  # leaves a 'running' job
        raised = False
        try:
            await eng._open_sync_job(cid, process=True)
        except ValueError as e:
            raised = "sync_already_running" in str(e)
        check("F8: concurrent sync rejected (sync_already_running)", raised)
        await eng._finalize_job(j1, None)  # release the slot

        # G2 — remove one connector, the other is untouched
        await eng.remove_connector(da)
        ra = await eng.search(
            "raft leader election", connector_uri=f"file://local{da}", mode="hybrid", top_k=5
        )
        check(
            "G2: removed connector returns nothing",
            ra == [] or not any("a.md" in (e["source"] or "") for e in ra),
        )
        rb = await eng.search(
            "columnar zonemap pruning", connector_uri=f"file://local{db}", mode="hybrid", top_k=5
        )
        check(
            "G2: surviving connector still searchable",
            any("b.md" in (e["source"] or "") for e in rb),
        )
        left = await eng.meta.fetchall("SELECT root_uri FROM connectors")
        check(
            "G2: only the surviving connector remains", len(left) == 1 and db in left[0]["root_uri"]
        )
    finally:
        try:
            eng.milvus.drop_collection("default")
        except Exception:
            pass
        await eng.shutdown()
        shutil.rmtree(da, ignore_errors=True)
        shutil.rmtree(db, ignore_errors=True)
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'=' * 46}\n  multi-connector: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
