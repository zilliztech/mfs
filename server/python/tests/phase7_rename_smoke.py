"""Phase 7 rename optimization — rename = chunk_id rewrite, reuse vectors (zero
re-embed). Needs OPENAI_API_KEY (bash -ic). Lite.
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


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic")
        raise SystemExit(2)
    root = tempfile.mkdtemp(prefix="mfs_p7r_")
    open(f"{root}/notes.md", "w").write("# Networking\n\nTCP congestion control and retransmission timers.\n")
    base = f"/tmp/mfs_p7r_{os.getpid()}"
    os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_meta.db"; cfg.milvus.uri = base + "_milvus.db"; cfg.milvus.token = ""
    cfg.object_store.root = base + "_cache"; cfg.transformation_cache.db_path = base + "_tx.db"
    eng = Engine(cfg)
    await eng.startup()
    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")
        conn_uri = f"file://local{root}"
        await eng.add(root)
        calls1 = eng.embed.api_calls
        c0 = await asyncio.to_thread(eng.milvus.count, "default")
        check("rename: initial index embedded", calls1 > 0 and c0 > 0)

        os.rename(f"{root}/notes.md", f"{root}/networking.md")
        job = await eng.add(root)
        tk = await eng.meta.fetchall("SELECT change_kind FROM object_tasks WHERE connector_job_id=?", (job,))
        check("rename: detected as renamed", any(t["change_kind"] == "renamed" for t in tk))
        check("rename: ZERO new embedding API calls (vectors reused)", eng.embed.api_calls == calls1)
        c1 = await asyncio.to_thread(eng.milvus.count, "default")
        check("rename: chunk count unchanged", c1 == c0)

        conn = await eng.meta.fetchone("SELECT id FROM connectors WHERE type='file'")
        gone = await eng.meta.fetchone("SELECT 1 FROM objects WHERE connector_id=? AND object_uri='/notes.md'", (conn["id"],))
        newo = await eng.meta.fetchone("SELECT chunk_count FROM objects WHERE connector_id=? AND object_uri='/networking.md'", (conn["id"],))
        check("rename: old object_uri gone", gone is None)
        check("rename: new object_uri present with chunks", newo and newo["chunk_count"] > 0)

        res = await eng.search("tcp congestion retransmission", connector_uri=conn_uri, mode="hybrid", top_k=3)
        check("rename: search hits new uri", any("networking.md" in (e["source"] or "") for e in res)
              and not any("notes.md" in (e["source"] or "") for e in res))
    finally:
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        await eng.shutdown(); shutil.rmtree(root, ignore_errors=True); os.system(f"rm -rf '{base}'*")

    passed = sum(1 for _, c in results if c)
    total = len(results)
    print(f"\n{'='*40}\nPhase 7 rename: {passed}/{total} checks passed")
    if passed != total:
        print("FAILED:", [n for n, c in results if not c]); raise SystemExit(1)
    print("ALL PASS")


if __name__ == "__main__":
    asyncio.run(main())
