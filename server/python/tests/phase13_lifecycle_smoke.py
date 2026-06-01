"""Phase 13 — incremental lifecycle matrix (D1 / D4 / D7 / D12), file connector, Lite.

  D1 no-change re-add  -> 0 tasks, 0 new embeddings
  D4 modify one file   -> only it re-embeds; search reflects new content
  D7 delete one file   -> its chunks + object row purged, no longer retrievable
  D12 --force-index    -> full rebuild, transformation cache hit (0 new embeddings)
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
    results.append(bool(cond))
    print(f"  [{OK if cond else FAIL}] {name}")
    return cond


async def _tasks_for(eng, job_id):
    r = await eng.meta.fetchall(
        "SELECT change_kind FROM object_tasks WHERE connector_job_id=?", (job_id,)
    )
    return [x["change_kind"] for x in r]


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic")
        raise SystemExit(2)
    root = tempfile.mkdtemp(prefix="mfs_life_")
    open(f"{root}/a.md", "w").write("# Alpha\n\nThe alpha module handles ingestion.\n")
    open(f"{root}/b.md", "w").write("# Beta\n\nThe beta module handles retrieval.\n")
    open(f"{root}/c.md", "w").write("# Gamma\n\nThe gamma module handles export.\n")
    base = f"/tmp/mfs_life_{os.getpid()}"
    os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"
    cfg.milvus.uri = base + "_v.db"
    cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"
    cfg.transformation_cache.db_path = base + "_t.db"
    cfg.summary.enabled = False  # this suite tests file lifecycle, not summaries (hermetic)
    eng = Engine(cfg)
    await eng.startup()
    conn_uri = f"file://local{root}"
    try:
        eng.milvus.drop_collection("default")
        eng.milvus.ensure_collection("default")
        await eng.add(root)
        c0 = await asyncio.to_thread(eng.milvus.count, "default")
        calls0 = eng.embed.api_calls
        check("initial: chunks indexed", c0 > 0 and calls0 > 0)

        # D1 — no-change re-add
        job = await eng.add(root, full=False)
        check("D1 no-change re-add: 0 tasks", len(await _tasks_for(eng, job)) == 0)
        check("D1 no-change re-add: 0 new embeddings", eng.embed.api_calls == calls0)

        # D4 — modify one file
        calls1 = eng.embed.api_calls
        open(f"{root}/b.md", "w").write("# Beta\n\nBeta now performs vector reranking.\n")
        job = await eng.add(root, full=False)
        tk = await _tasks_for(eng, job)
        check("D4 modify: exactly one modified task", tk == ["modified"])
        check("D4 modify: re-embedded (new calls)", eng.embed.api_calls > calls1)
        res = await eng.search("vector reranking", connector_uri=conn_uri, mode="hybrid", top_k=3)
        check(
            "D4 modify: search reflects new content",
            any("b.md" in (e["source"] or "") for e in res),
        )

        # D7 — delete one file
        cbefore = await asyncio.to_thread(eng.milvus.count, "default")
        os.remove(f"{root}/c.md")
        job = await eng.add(root, full=False)
        tk = await _tasks_for(eng, job)
        check("D7 delete: one deleted task", tk == ["deleted"])
        cafter = await asyncio.to_thread(eng.milvus.count, "default")
        check("D7 delete: chunk count dropped", cafter < cbefore)
        gone = await eng.meta.fetchone(
            "SELECT 1 FROM objects o JOIN connectors c ON o.connector_id=c.id "
            "WHERE c.root_uri=? AND o.object_uri='/c.md'",
            (conn_uri,),
        )
        check("D7 delete: object row gone", gone is None)
        res = await eng.search(
            "gamma module export", connector_uri=conn_uri, mode="hybrid", top_k=5
        )
        check("D7 delete: not retrievable", not any("c.md" in (e["source"] or "") for e in res))

        # D12 — force-index = full rebuild, transformation cache hit
        calls2 = eng.embed.api_calls
        await eng.add(root, full=True)
        check(
            "D12 force-index: rebuilt, 0 new embeddings (cache hit)", eng.embed.api_calls == calls2
        )
    finally:
        try:
            eng.milvus.drop_collection("default")
        except Exception:
            pass
        await eng.shutdown()
        shutil.rmtree(root, ignore_errors=True)
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'=' * 46}\n  incremental lifecycle: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
