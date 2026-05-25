"""Phase 2 engine end-to-end smoke — `.venv/bin/python tests/phase2_engine_smoke.py`.

Drives Engine.add on a real temp dir and asserts cross-store state: connectors row,
objects rows (search_status), file_state (indexed), connector_jobs (succeeded),
object_tasks (succeeded). Then re-add (idempotent, 0 tasks) and modify (1 task).
Milvus = Lite (collection ensured at startup; dropped in cleanup). No embedding yet
(Phase 2 stub).
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
    root = tempfile.mkdtemp(prefix="mfs_eng_test_")
    os.makedirs(f"{root}/src")
    open(f"{root}/a.md", "w").write("hello world")
    open(f"{root}/src/b.py", "w").write("def f():\n    return 1\n")
    open(f"{root}/ignore.log", "w").write("noise")
    open(f"{root}/.gitignore", "w").write("*.log\n")

    base = f"/tmp/mfs_eng_{os.getpid()}"
    for suf in ("_meta.db", "_meta.db-wal", "_meta.db-shm", "_tx.db"):
        if os.path.exists(base + suf):
            os.remove(base + suf)
    os.system(f"rm -rf '{base}_milvus.db'* '{base}_cache'")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_meta.db"
    cfg.milvus.uri = base + "_milvus.db"
    cfg.milvus.token = ""
    cfg.object_store.root = base + "_cache"
    cfg.transformation_cache.db_path = base + "_tx.db"

    eng = Engine(cfg)
    await eng.startup()
    try:
        # 1. first add
        job_id = await eng.add(root)
        conn = await eng.meta.fetchone("SELECT * FROM connectors WHERE type='file'")
        check("connector registered", conn is not None and conn["root_uri"] == f"file://local{root}")
        cid = conn["id"]
        objs = await eng.meta.fetchall("SELECT * FROM objects WHERE connector_id=?", (cid,))
        ouris = {o["object_uri"]: o for o in objs}
        check("objects: /a.md indexed", ouris.get("/a.md", {}).get("search_status") == "indexed")
        check("objects: /src/b.py indexed", "/src/b.py" in ouris)
        check("objects: /ignore.log absent (.gitignore)", "/ignore.log" not in ouris)
        job = await eng.meta.fetchone("SELECT * FROM connector_jobs WHERE id=?", (job_id,))
        check("job succeeded", job["status"] == "succeeded")
        check("job succeeded_objects == 3", job["succeeded_objects"] == 3)
        tasks = await eng.meta.fetchall("SELECT * FROM object_tasks WHERE connector_job_id=?", (job_id,))
        check("all tasks succeeded", all(t["status"] == "succeeded" for t in tasks) and len(tasks) == 3)
        fs = await eng.meta.fetchall("SELECT * FROM file_state WHERE connector_id=?", (cid,))
        check("file_state all indexed", all(r["status"] == "indexed" for r in fs) and len(fs) == 3)

        # 2. re-add, no change -> 0 tasks
        job2 = await eng.add(root)
        t2 = await eng.meta.fetchall("SELECT * FROM object_tasks WHERE connector_job_id=?", (job2,))
        check("re-add idempotent: 0 tasks", len(t2) == 0)

        # 3. modify a.md -> 1 modified task
        open(f"{root}/a.md", "w").write("hello world CHANGED bigger content")
        job3 = await eng.add(root)
        t3 = await eng.meta.fetchall("SELECT * FROM object_tasks WHERE connector_job_id=?", (job3,))
        check("modify -> 1 task", len(t3) == 1 and t3[0]["change_kind"] == "modified" and t3[0]["status"] == "succeeded")
    finally:
        try:
            eng.milvus.drop_collection(cfg.namespace)
        except Exception:
            pass
        await eng.shutdown()
        shutil.rmtree(root, ignore_errors=True)
        os.system(f"rm -rf '{base}'*")

    passed = sum(1 for _, c in results if c)
    total = len(results)
    print(f"\n{'='*40}\nPhase 2 engine: {passed}/{total} checks passed")
    if passed != total:
        print("FAILED:", [n for n, c in results if not c])
        raise SystemExit(1)
    print("ALL PASS")


if __name__ == "__main__":
    asyncio.run(main())
