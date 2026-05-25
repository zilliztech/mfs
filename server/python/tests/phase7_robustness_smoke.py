"""Phase 7 robustness/reliability smoke — needs OPENAI_API_KEY (bash -ic). Lite.

A) model change + force-index: cache invalidates (re-embed), re-run hits cache.
B) deletion consistency: remove a file -> next add deletes its objects + Milvus chunks.
C) failure + recovery: an object's indexing fails once (task=failed, file_state stays
   staged, not indexed); next add retries and succeeds (objects indexed, chunks present).
Monitors object_tasks state machine, objects rows, Milvus count, embed api/cache counters.
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
    results.append((name, cond))
    print(f"  [{OK if cond else FAIL}] {name}")
    return cond


def mkcfg(base):
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_meta.db"
    cfg.milvus.uri = base + "_milvus.db"
    cfg.milvus.token = ""
    cfg.object_store.root = base + "_cache"
    cfg.transformation_cache.db_path = base + "_tx.db"
    cfg.worker.backoff_initial_ms = 0      # no real backoff sleep in tests
    return cfg


async def case_a_model_change():
    print("== A) model change + force-index (cache invalidation) ==")
    root = tempfile.mkdtemp(prefix="mfs_p7a_")
    open(f"{root}/doc.md", "w").write("# Auth\n\nSessions use Redis with a TTL and OAuth tokens.\n")
    base = f"/tmp/mfs_p7a_{os.getpid()}"
    os.system(f"rm -rf '{base}'*")
    eng = Engine(mkcfg(base))
    await eng.startup()
    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")
        await eng.add(root)
        calls1 = eng.embed.api_calls
        check("A: first add embedded", calls1 > 0)
        # simulate switching embedding model: version bump -> cache keys change
        eng.embed.version = "model-v2"
        await eng.add(root, full=True)        # force-index: re-chunk same text, new model -> all miss
        calls2 = eng.embed.api_calls
        check("A: model change force-index re-embeds (cache miss)", calls2 > calls1)
        # force-index again with same (new) model -> cache hit, no new calls
        await eng.add(root, full=True)
        check("A: re-run same model hits cache (no new API calls)", eng.embed.api_calls == calls2)
    finally:
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        await eng.shutdown(); shutil.rmtree(root, ignore_errors=True); os.system(f"rm -rf '{base}'*")


async def case_b_deletion():
    print("== B) deletion consistency ==")
    root = tempfile.mkdtemp(prefix="mfs_p7b_")
    open(f"{root}/keep.md", "w").write("# Keep\n\nDatabase indexing strategies.\n")
    open(f"{root}/gone.md", "w").write("# Gone\n\nThis file will be deleted soon.\n")
    base = f"/tmp/mfs_p7b_{os.getpid()}"
    os.system(f"rm -rf '{base}'*")
    eng = Engine(mkcfg(base))
    await eng.startup()
    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")
        await eng.add(root)
        conn = await eng.meta.fetchone("SELECT id FROM connectors WHERE type='file'")
        n0 = len(await eng.meta.fetchall("SELECT 1 FROM objects WHERE connector_id=?", (conn["id"],)))
        c0 = await asyncio.to_thread(eng.milvus.count, "default")
        check("B: both files indexed", n0 == 2 and c0 >= 2)
        os.remove(f"{root}/gone.md")
        job = await eng.add(root)
        tk = await eng.meta.fetchall("SELECT change_kind FROM object_tasks WHERE connector_job_id=?", (job,))
        check("B: deletion produced a 'deleted' task", any(t["change_kind"] == "deleted" for t in tk))
        gone = await eng.meta.fetchone("SELECT 1 FROM objects WHERE connector_id=? AND object_uri='/gone.md'", (conn["id"],))
        check("B: gone.md removed from objects", gone is None)
        c1 = await asyncio.to_thread(eng.milvus.count, "default")
        check("B: Milvus chunk count decreased", c1 < c0)
        # search no longer returns gone.md
        res = await eng.search("file will be deleted", connector_uri=f"file://local{root}", mode="hybrid", top_k=5)
        check("B: search no longer returns gone.md", not any("gone.md" in (e["source"] or "") for e in res))
    finally:
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        await eng.shutdown(); shutil.rmtree(root, ignore_errors=True); os.system(f"rm -rf '{base}'*")


async def case_c_failure_recovery():
    print("== C) failure + recovery (job inheritance / staged retry) ==")
    root = tempfile.mkdtemp(prefix="mfs_p7c_")
    open(f"{root}/good.md", "w").write("# Good\n\nStable content about networking.\n")
    open(f"{root}/flaky.md", "w").write("# Flaky\n\nContent that fails to index the first time.\n")
    base = f"/tmp/mfs_p7c_{os.getpid()}"
    os.system(f"rm -rf '{base}'*")
    eng = Engine(mkcfg(base))
    await eng.startup()
    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")
        # monkeypatch _index_object to fail once on flaky.md
        orig = eng._index_object
        state = {"count": 0}

        async def flaky_index(plugin, connector_uri, task):
            if task["object_uri"] == "/flaky.md":
                state["count"] += 1
                if state["count"] <= eng.cfg.worker.max_retries + 1:  # exhaust retries on first add
                    raise RuntimeError("simulated transient indexing failure")
            return await orig(plugin, connector_uri, task)

        eng._index_object = flaky_index
        await eng.add(root)
        conn = await eng.meta.fetchone("SELECT id FROM connectors WHERE type='file'")
        flaky = await eng.meta.fetchone("SELECT search_status FROM objects WHERE connector_id=? AND object_uri='/flaky.md'", (conn["id"],))
        failed = await eng.meta.fetchall("SELECT 1 FROM object_tasks WHERE object_uri='/flaky.md' AND status='failed'")
        check("C: flaky.md task failed first run", len(failed) >= 1)
        check("C: flaky.md not indexed yet", flaky is None or flaky["search_status"] != "indexed")
        good = await eng.meta.fetchone("SELECT search_status FROM objects WHERE connector_id=? AND object_uri='/good.md'", (conn["id"],))
        check("C: good.md indexed (other tasks unaffected)", good and good["search_status"] == "indexed")

        # recovery: next add (flaky no longer fails) — staged file re-yielded + failed task inherited
        await eng.add(root)
        flaky2 = await eng.meta.fetchone("SELECT search_status, chunk_count FROM objects WHERE connector_id=? AND object_uri='/flaky.md'", (conn["id"],))
        check("C: flaky.md recovered (indexed with chunks)", flaky2 and flaky2["search_status"] == "indexed" and flaky2["chunk_count"] > 0)
    finally:
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        await eng.shutdown(); shutil.rmtree(root, ignore_errors=True); os.system(f"rm -rf '{base}'*")


async def case_d_circuit_breaker():
    print("== D) circuit breaker on consecutive fatal (quota) ==")
    root = tempfile.mkdtemp(prefix="mfs_p7d_")
    for i in range(8):
        open(f"{root}/f{i}.md", "w").write(f"# Doc {i}\n\nContent number {i} about distributed systems.\n")
    base = f"/tmp/mfs_p7d_{os.getpid()}"
    os.system(f"rm -rf '{base}'*")
    eng = Engine(mkcfg(base))
    await eng.startup()
    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")

        async def quota_fail(texts):
            raise RuntimeError("Error code: 429 - insufficient_quota: You exceeded your current quota")

        eng.embed.batch_embed = quota_fail      # simulate API key out of quota
        job = await eng.add(root)
        jr = await eng.meta.fetchone("SELECT status, error FROM connector_jobs WHERE id=?", (job,))
        check("D: job failed via circuit breaker", jr["status"] == "failed" and jr["error"] == "circuit_breaker_tripped")
        cancelled = await eng.meta.fetchall("SELECT 1 FROM object_tasks WHERE connector_job_id=? AND status='cancelled'", (job,))
        check("D: remaining tasks cancelled (not wastefully run)", len(cancelled) > 0)
        failed = await eng.meta.fetchall("SELECT 1 FROM object_tasks WHERE connector_job_id=? AND status='failed'", (job,))
        check("D: fatal failures reached threshold", len(failed) >= eng.cfg.worker.consecutive_fatal_threshold)
    finally:
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        await eng.shutdown(); shutil.rmtree(root, ignore_errors=True); os.system(f"rm -rf '{base}'*")


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic")
        raise SystemExit(2)
    await case_a_model_change()
    await case_b_deletion()
    await case_c_failure_recovery()
    await case_d_circuit_breaker()

    passed = sum(1 for _, c in results if c)
    total = len(results)
    print(f"\n{'='*40}\nPhase 7 robustness: {passed}/{total} checks passed")
    if passed != total:
        print("FAILED:", [n for n, c in results if not c])
        raise SystemExit(1)
    print("ALL PASS")


if __name__ == "__main__":
    asyncio.run(main())
