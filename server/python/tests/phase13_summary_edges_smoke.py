"""Phase 13 — round-2 directory-summary edges (R4.1/4.2/4.5/4.6, R5.4). Needs OPENAI_API_KEY. Lite."""
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
    cfg.summary.enabled = True
    return cfg


async def _has_dirsum(eng, conn_uri, rel):
    rows = await asyncio.to_thread(eng.milvus.get_chunks_by_object, "default", conn_uri, conn_uri + rel)
    return any(r.get("chunk_kind") == "directory_summary" for r in rows)


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic"); raise SystemExit(2)

    # ---- R4.1 binary-only subdir gets no summary ; R4.6 emptied dir purges its summary ----
    root = tempfile.mkdtemp(prefix="mfs_se_")
    os.makedirs(f"{root}/bins", exist_ok=True); os.makedirs(f"{root}/docs", exist_ok=True)
    with open(f"{root}/bins/blob.bin", "wb") as f:
        f.write(bytes(range(256)) * 8)
    open(f"{root}/docs/readme.md", "w").write("# Docs\n\nDeployment and rollback guide.\n")
    base = f"/tmp/mfs_se_{os.getpid()}"; os.system(f"rm -rf '{base}'*")
    eng = Engine(_mkcfg(base)); await eng.startup()
    conn_uri = f"file://local{root}"
    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")
        await eng.add(root)
        check("R4.1 binary-only /bins has NO directory_summary", not await _has_dirsum(eng, conn_uri, "/bins"))
        check("R4.1 /docs has a directory_summary", await _has_dirsum(eng, conn_uri, "/docs"))
        check("R4.1 root has a directory_summary", await _has_dirsum(eng, conn_uri, "/"))

        # R4.6 — empty out /docs, re-add, its summary should be purged
        os.remove(f"{root}/docs/readme.md")
        await eng.add(root, full=False)
        check("R4.6 emptied /docs -> directory_summary purged", not await _has_dirsum(eng, conn_uri, "/docs"))
    finally:
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        await eng.shutdown(); shutil.rmtree(root, ignore_errors=True); os.system(f"rm -rf '{base}'*")

    # ---- R4.2 deep nesting ; R4.5 truncation budget ----
    root2 = tempfile.mkdtemp(prefix="mfs_sed_")
    deep = root2 + "/a/b/c/d/e"; os.makedirs(deep, exist_ok=True)
    open(f"{deep}/leaf.md", "w").write("# Leaf\n\n" + ("payment idempotency key handling. " * 400))  # big
    base2 = f"/tmp/mfs_sed_{os.getpid()}"; os.system(f"rm -rf '{base2}'*")
    cfg2 = _mkcfg(base2); cfg2.summary.per_file_max_kb = 1; cfg2.summary.max_input_kb = 4
    eng2 = Engine(cfg2); await eng2.startup()
    conn2 = f"file://local{root2}"
    captured = {"max_in": 0}
    orig = eng2.summary.summarize

    async def spy(text, kind="directory_summary"):
        captured["max_in"] = max(captured["max_in"], len(text))
        return await orig(text, kind)
    eng2.summary.summarize = spy
    try:
        eng2.milvus.drop_collection("default"); eng2.milvus.ensure_collection("default")
        await eng2.add(root2)
        # ancestors of /a/b/c/d/e/leaf.md = /, /a, /a/b, /a/b/c, /a/b/c/d, /a/b/c/d/e  = 6 dirs
        n_dir = await asyncio.to_thread(eng2.milvus.count, "default", 'chunk_kind == "directory_summary"')
        check("R4.2 deep nesting: one summary per ancestor dir (6)", n_dir == 6)
        check("R4.2 deepest dir summarized", await _has_dirsum(eng2, conn2, "/a/b/c/d/e"))
        check("R4.5 summary input respected max_input_kb budget",
              0 < captured["max_in"] <= cfg2.summary.max_input_kb * 1024)
    finally:
        eng2.summary.summarize = orig
        try: eng2.milvus.drop_collection("default")
        except Exception: pass
        await eng2.shutdown(); shutil.rmtree(root2, ignore_errors=True); os.system(f"rm -rf '{base2}'*")

    # ---- R5.4 mtime-only change must not re-embed ----
    root3 = tempfile.mkdtemp(prefix="mfs_mt_")
    open(f"{root3}/x.md", "w").write("# X\n\nstable content, only the mtime will move.\n")
    base3 = f"/tmp/mfs_mt_{os.getpid()}"; os.system(f"rm -rf '{base3}'*")
    cfg3 = _mkcfg(base3); cfg3.summary.enabled = False
    eng3 = Engine(cfg3); await eng3.startup()
    try:
        eng3.milvus.drop_collection("default"); eng3.milvus.ensure_collection("default")
        await eng3.add(root3)
        calls = eng3.embed.api_calls
        os.utime(f"{root3}/x.md", (10**9, 10**9))     # change mtime, identical content
        job = await eng3.add(root3, full=False)
        tk = await eng3.meta.fetchall("SELECT change_kind FROM object_tasks WHERE connector_job_id=?", (job,))
        check("R5.4 mtime-only change -> 0 tasks (sha1 fingerprint)", len(tk) == 0)
        check("R5.4 mtime-only change -> 0 new embeddings", eng3.embed.api_calls == calls)
    finally:
        try: eng3.milvus.drop_collection("default")
        except Exception: pass
        await eng3.shutdown(); shutil.rmtree(root3, ignore_errors=True); os.system(f"rm -rf '{base3}'*")

    passed = sum(results)
    print(f"\n{'='*46}\n  summary edges (R4/R5): {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
