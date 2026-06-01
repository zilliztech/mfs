"""Phase 11 — standalone worker daemon + cancel. No SaaS keys (file connector).
Needs OPENAI_API_KEY (bash -ic). Milvus Lite.

  A) enqueue (process=False) -> job 'queued', objects NOT yet indexed; run_worker_once
     -> job 'succeeded', objects indexed.
  B) cancel: enqueue, cancel_job -> tasks+job 'cancelled'; run_worker_once is a no-op
     (no queued job); objects stay not-indexed.
"""

import asyncio
import os

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append(bool(cond))
    print(f"  [{OK if cond else FAIL}] {name}")
    return cond


async def _indexed_count(eng, cid):
    rows = await eng.meta.fetchall(
        "SELECT count(*) AS n FROM objects WHERE connector_id=? AND search_status='indexed'", (cid,)
    )
    return rows[0]["n"] if rows else 0


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic")
        raise SystemExit(2)
    base = f"/tmp/mfs_wk_{os.getpid()}"
    os.system(f"rm -rf '{base}'*")
    repo = base + "_repo"
    os.makedirs(repo)
    for i in range(3):
        open(f"{repo}/doc{i}.md", "w").write(
            f"# Doc {i}\nSingle sign-on note number {i} about SAML tokens.\n"
        )

    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_meta.db"
    cfg.milvus.uri = base + "_milvus.db"
    cfg.milvus.token = ""
    cfg.object_store.root = base + "_cache"
    cfg.transformation_cache.db_path = base + "_tx.db"
    eng = Engine(cfg)
    await eng.startup()
    try:
        eng.milvus.drop_collection("default")
        eng.milvus.ensure_collection("default")

        # A) enqueue without processing
        job_id = await eng.add(repo, process=False)
        jrow = await eng.meta.fetchone("SELECT status FROM connector_jobs WHERE id=?", (job_id,))
        check("A: job is 'queued' after enqueue", jrow and jrow["status"] == "queued")
        cid = (await eng.meta.fetchone("SELECT id FROM connectors WHERE type='file'"))["id"]
        pending = await eng.meta.fetchall(
            "SELECT count(*) AS n FROM object_tasks WHERE connector_job_id=? AND status='pending'",
            (job_id,),
        )
        check("A: 3 tasks pending, none processed", pending[0]["n"] == 3)
        check("A: 0 objects indexed yet", await _indexed_count(eng, cid) == 0)

        # run the worker once -> drains the queued job
        done = await eng.run_worker_once()
        check("A: worker_once claimed the job", done == job_id)
        jrow = await eng.meta.fetchone(
            "SELECT status, succeeded_objects FROM connector_jobs WHERE id=?", (job_id,)
        )
        check(
            "A: job 'succeeded' after worker",
            jrow["status"] == "succeeded" and jrow["succeeded_objects"] == 3,
        )
        check("A: 3 objects indexed", await _indexed_count(eng, cid) == 3)
        check("A: worker_once now returns None (queue empty)", await eng.run_worker_once() is None)

        # B) cancel a freshly enqueued job
        repo2 = base + "_repo2"
        os.makedirs(repo2)
        for i in range(2):
            open(f"{repo2}/x{i}.md", "w").write(f"# X{i}\ncontent {i}\n")
        job2 = await eng.add(repo2, process=False)
        ok = await eng.cancel_job(job2)
        check("B: cancel_job returns True", ok is True)
        j2 = await eng.meta.fetchone("SELECT status FROM connector_jobs WHERE id=?", (job2,))
        check("B: job 'cancelled'", j2["status"] == "cancelled")
        ctasks = await eng.meta.fetchall(
            "SELECT count(*) AS n FROM object_tasks WHERE connector_job_id=? AND status='cancelled'",
            (job2,),
        )
        check("B: tasks cancelled", ctasks[0]["n"] == 2)
        check("B: worker_once skips cancelled job (None)", await eng.run_worker_once() is None)
        cid2 = (
            await eng.meta.fetchone(
                "SELECT id FROM connectors WHERE root_uri LIKE ?", ("%" + os.path.basename(repo2),)
            )
        )["id"]
        check("B: cancelled connector's objects not indexed", await _indexed_count(eng, cid2) == 0)
    finally:
        try:
            eng.milvus.drop_collection("default")
        except Exception:
            pass
        await eng.shutdown()
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'=' * 46}\n  worker + cancel: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
