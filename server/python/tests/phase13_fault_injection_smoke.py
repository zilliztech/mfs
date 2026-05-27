"""Phase 13 — fault injection / resilience (matrix J1 / J2 / J8 / F6). Lite.

Monkeypatches the embedding client to drive the worker's error handling:
  J1 transient (one 429 then ok) -> retried, task succeeds
  J2 fatal (quota/auth)          -> no retry, task failed immediately
  J8 partial failure             -> one bad object fails, the rest succeed
  F6 circuit breaker             -> consecutive failures past threshold abort the job
Needs OPENAI_API_KEY (real embeddings on the success paths). Lite.
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


def _mkcfg(base):
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"; cfg.milvus.uri = base + "_v.db"; cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"; cfg.transformation_cache.db_path = base + "_t.db"
    cfg.summary.enabled = False
    cfg.worker.backoff_initial_ms = 5; cfg.worker.backoff_max_ms = 10     # keep retries fast
    return cfg


async def _job_tasks(eng, job):
    rows = await eng.meta.fetchall("SELECT status, last_error FROM object_tasks WHERE connector_job_id=?", (job,))
    return rows


async def case_transient():
    base = f"/tmp/mfs_fj_t_{os.getpid()}"; os.system(f"rm -rf '{base}'*")
    root = tempfile.mkdtemp(prefix="mfs_fj_t_")
    open(f"{root}/x.md", "w").write("# X\n\nbackoff and retry on a transient error.\n")
    eng = Engine(_mkcfg(base)); await eng.startup()
    orig = eng.embed.batch_embed
    state = {"n": 0}

    async def flaky(texts):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("429 rate limit exceeded, please retry")
        return await orig(texts)
    eng.embed.batch_embed = flaky
    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")
        job = await eng.add(root)
        tk = await _job_tasks(eng, job)
        check("J1 transient: task eventually succeeded", all(t["status"] == "succeeded" for t in tk) and len(tk) == 1)
        check("J1 transient: a retry actually happened", state["n"] >= 2)
    finally:
        eng.embed.batch_embed = orig
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        await eng.shutdown(); shutil.rmtree(root, ignore_errors=True); os.system(f"rm -rf '{base}'*")


async def case_fatal():
    base = f"/tmp/mfs_fj_f_{os.getpid()}"; os.system(f"rm -rf '{base}'*")
    root = tempfile.mkdtemp(prefix="mfs_fj_f_")
    open(f"{root}/x.md", "w").write("# X\n\nfatal quota path.\n")
    eng = Engine(_mkcfg(base)); await eng.startup()
    calls = {"n": 0}

    async def boom(texts):
        calls["n"] += 1
        raise RuntimeError("insufficient_quota: you exceeded your current quota")
    eng.embed.batch_embed = boom
    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")
        job = await eng.add(root)
        tk = await _job_tasks(eng, job)
        check("J2 fatal: task failed", len(tk) == 1 and tk[0]["status"] == "failed")
        check("J2 fatal: no retry (single embed attempt)", calls["n"] == 1)
        check("J2 fatal: error recorded", "fatal" in (tk[0]["last_error"] or ""))
    finally:
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        await eng.shutdown(); shutil.rmtree(root, ignore_errors=True); os.system(f"rm -rf '{base}'*")


async def case_partial():
    base = f"/tmp/mfs_fj_p_{os.getpid()}"; os.system(f"rm -rf '{base}'*")
    root = tempfile.mkdtemp(prefix="mfs_fj_p_")
    open(f"{root}/good.md", "w").write("# Good\n\nthis one indexes fine.\n")
    open(f"{root}/bad.md", "w").write("# Bad\n\nPOISON marker triggers a fatal embed.\n")
    eng = Engine(_mkcfg(base)); await eng.startup()
    orig = eng.embed.batch_embed

    async def selective(texts):
        if any("POISON" in t for t in texts):
            raise RuntimeError("insufficient_quota: poisoned object")
        return await orig(texts)
    eng.embed.batch_embed = selective
    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")
        job = await eng.add(root)
        row = await eng.meta.fetchone(
            "SELECT succeeded_objects, failed_objects, total_objects FROM connector_jobs WHERE id=?", (job,))
        check("J8 partial: 1 succeeded", row["succeeded_objects"] == 1)
        check("J8 partial: 1 failed", row["failed_objects"] == 1)
        res = await eng.search("this one indexes fine", connector_uri=f"file://local{root}", mode="hybrid", top_k=5)
        check("J8 partial: good object still retrievable", any("good.md" in (e["source"] or "") for e in res))
    finally:
        eng.embed.batch_embed = orig
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        await eng.shutdown(); shutil.rmtree(root, ignore_errors=True); os.system(f"rm -rf '{base}'*")


async def case_breaker():
    base = f"/tmp/mfs_fj_b_{os.getpid()}"; os.system(f"rm -rf '{base}'*")
    root = tempfile.mkdtemp(prefix="mfs_fj_b_")
    for i in range(8):
        open(f"{root}/f{i}.md", "w").write(f"# F{i}\n\ncontent {i}\n")
    cfg = _mkcfg(base); cfg.worker.consecutive_fatal_threshold = 3
    eng = Engine(cfg); await eng.startup()

    async def boom(texts):
        raise RuntimeError("insufficient_quota: everything fails")
    eng.embed.batch_embed = boom
    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")
        job = await eng.add(root)
        row = await eng.meta.fetchone("SELECT status, error FROM connector_jobs WHERE id=?", (job,))
        check("F6 breaker: job aborted", row["status"] == "failed")
        check("F6 breaker: reason is circuit breaker", "circuit_breaker" in (row["error"] or ""))
        cancelled = await eng.meta.fetchone(
            "SELECT count(*) AS n FROM object_tasks WHERE connector_job_id=? AND status='cancelled'", (job,))
        check("F6 breaker: remaining tasks cancelled (not all attempted)", cancelled["n"] > 0)
    finally:
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        await eng.shutdown(); shutil.rmtree(root, ignore_errors=True); os.system(f"rm -rf '{base}'*")


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic"); raise SystemExit(2)
    await case_transient()
    await case_fatal()
    await case_partial()
    await case_breaker()
    passed = sum(results)
    print(f"\n{'='*46}\n  fault injection: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
