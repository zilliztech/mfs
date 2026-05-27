"""Phase 13 — multi-worker concurrency on a shared metadata backend (matrix F4).

Postgres metadata (the shared queue) + Zilliz (thread-safe) in an isolated namespace: two
connectors are enqueued, then two workers claim concurrently. The conditional claim must be
race-free — each worker gets a DISTINCT job, both succeed, neither double-processes.
Needs OPENAI_API_KEY + ZILLIZ_URI/_API_KEY + local Postgres.
"""
import asyncio
import os
import shutil
import tempfile

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []
PG_DSN = "postgresql://zhangchen@/mfstest?host=/var/run/postgresql"


def check(name, cond):
    results.append(bool(cond)); print(f"  [{OK if cond else FAIL}] {name}"); return cond


async def main():
    uri = os.environ.get("ZILLIZ_URI"); tok = os.environ.get("ZILLIZ_API_KEY")
    if not os.environ.get("OPENAI_API_KEY") or not uri or not tok:
        print("need OPENAI_API_KEY + ZILLIZ_URI/_API_KEY"); raise SystemExit(2)
    ns = f"cc{os.getpid()}"
    da = tempfile.mkdtemp(prefix="mfs_cc_a_"); db = tempfile.mkdtemp(prefix="mfs_cc_b_")
    open(f"{da}/a.md", "w").write("# Alpha\n\nraft consensus and leader election.\n")
    open(f"{db}/b.md", "w").write("# Beta\n\ncolumnar storage and zonemap pruning.\n")
    base = f"/tmp/mfs_cc_{os.getpid()}"; os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.namespace = ns
    cfg.metadata.backend = "postgres"; cfg.metadata.dsn = PG_DSN
    cfg.milvus.uri = uri; cfg.milvus.token = tok; cfg.milvus.collection_strategy = "per_namespace"
    cfg.object_store.root = base + "_c"; cfg.transformation_cache.db_path = base + "_t.db"
    cfg.worker.concurrency = 2; cfg.summary.enabled = False
    eng = Engine(cfg)
    await eng.startup()
    try:
        # enqueue two connectors (process=False -> queued, not yet indexed)
        ja = await eng.add(da, process=False)
        jb = await eng.add(db, process=False)
        check("two jobs queued", ja != jb)

        # two workers claim concurrently
        claimed = await asyncio.gather(eng.run_worker_once(), eng.run_worker_once())
        got = {c for c in claimed if c}
        check("F4: two workers claimed two DISTINCT jobs (race-free)", got == {ja, jb})

        # both jobs succeeded, counts agree, no double-processing
        for jid, label in ((ja, "alpha"), (jb, "beta")):
            jr = await eng.meta.fetchone(
                "SELECT status, total_objects, succeeded_objects FROM connector_jobs WHERE id=?", (jid,))
            check(f"F4: job {label} succeeded, total==succeeded",
                  jr["status"] == "succeeded" and jr["total_objects"] == jr["succeeded_objects"])

        ra = await eng.search("raft leader election", connector_uri=f"file://local{da}", mode="hybrid", top_k=3)
        rb = await eng.search("columnar zonemap pruning", connector_uri=f"file://local{db}", mode="hybrid", top_k=3)
        check("F4: both connectors' data searchable in parallel-built index",
              any("a.md" in (e["source"] or "") for e in ra) and any("b.md" in (e["source"] or "") for e in rb))
    finally:
        for d in (da, db):
            try: await eng.remove_connector(d)
            except Exception: pass
        try: eng.milvus.drop_collection(ns)
        except Exception: pass
        await eng.shutdown()
        shutil.rmtree(da, ignore_errors=True); shutil.rmtree(db, ignore_errors=True); os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'='*46}\n  PG multi-worker concurrency: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
