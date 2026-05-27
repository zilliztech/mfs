"""Phase 13 — full-state observability / cross-store consistency.

After add (and after modify / delete / remove) every backend table AND the vector DB must
agree: objects.chunk_count == Milvus chunks for that object_uri; tasks all terminal-succeeded;
job counts match; artifacts present then purged; remove leaves NOTHING anywhere. Needs
OPENAI_API_KEY (embed + VLM + summary). Lite.
"""
import asyncio
import os
import shutil
import tempfile

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []
TABLES = ["connectors", "objects", "object_tasks", "connector_jobs", "artifact_cache",
          "file_state", "connector_state"]


def check(name, cond):
    results.append(bool(cond)); print(f"  [{OK if cond else FAIL}] {name}"); return cond


async def _mcount(eng, expr=""):
    return await asyncio.to_thread(eng.milvus.count, "default", expr)


async def _obj_count(eng, cid, rel):
    r = await eng.meta.fetchone(
        "SELECT chunk_count FROM objects WHERE connector_id=? AND object_uri=?", (cid, rel))
    return r["chunk_count"] if r else None


def _lit(s):  # mirror milvus._lit for the expr
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic"); raise SystemExit(2)
    from PIL import Image
    root = tempfile.mkdtemp(prefix="mfs_state_")
    open(f"{root}/notes.md", "w").write("# Notes\n\nThe billing subsystem and invoice retries.\n")
    open(f"{root}/app.py", "w").write("def charge(inv):\n    return gateway.capture(inv)\n")
    Image.new("RGB", (80, 60), "navy").save(f"{root}/pic.png")
    with open(f"{root}/data.bin", "wb") as f:
        f.write(bytes(range(256)) * 4)
    base = f"/tmp/mfs_state_{os.getpid()}"; os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"; cfg.milvus.uri = base + "_v.db"; cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"; cfg.transformation_cache.db_path = base + "_t.db"
    cfg.summary.enabled = True
    eng = Engine(cfg)
    await eng.startup()
    cu = f"file://local{root}"
    full = lambda rel: cu + rel
    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")
        await eng.add(root)
        cid = (await eng.meta.fetchone("SELECT id, status FROM connectors WHERE root_uri=?", (cu,)))["id"]

        # ---- after ADD: cross-store consistency ----
        crow = await eng.meta.fetchone("SELECT count(*) AS n FROM connectors")
        check("connectors: exactly 1", crow["n"] == 1)
        st = (await eng.meta.fetchone("SELECT status FROM connectors WHERE id=?", (cid,)))["status"]
        check("connector status active", st == "active")

        objs = await eng.meta.fetchall("SELECT object_uri, chunk_count FROM objects WHERE connector_id=?", (cid,))
        ouris = {o["object_uri"] for o in objs}
        check("objects: one row per file (4)", ouris == {"/notes.md", "/app.py", "/pic.png", "/data.bin"})
        check("objects: binary has 0 chunks", await _obj_count(eng, cid, "/data.bin") == 0)
        nonbin = [await _obj_count(eng, cid, r) for r in ("/notes.md", "/app.py", "/pic.png")]
        check("objects: md/code/image > 0 chunks", all(c and c >= 1 for c in nonbin))

        # per-object: objects.chunk_count == Milvus chunks for that object_uri (THE invariant)
        consistent = True
        for o in objs:
            mc = await _mcount(eng, f'object_uri == "{_lit(full(o["object_uri"]))}"')
            if mc != o["chunk_count"]:
                consistent = False; print(f"      mismatch {o['object_uri']}: objects={o['chunk_count']} milvus={mc}")
        check("per-object chunk_count == Milvus count (all files)", consistent)

        # Milvus total == sum(file chunk_counts) + directory_summary count
        ndir = await _mcount(eng, 'chunk_kind == "directory_summary"')
        total = await _mcount(eng)
        check("Milvus total == file chunks + dir summaries",
              total == sum(o["chunk_count"] for o in objs) + ndir)

        # tasks: all terminal-succeeded, none stuck
        tk = await eng.meta.fetchall("SELECT status, count(*) AS n FROM object_tasks WHERE connector_id=? GROUP BY status", (cid,))
        tmap = {r["status"]: r["n"] for r in tk}
        check("object_tasks: all succeeded, none pending/failed/cancelled",
              tmap.get("succeeded", 0) > 0 and not (tmap.get("pending") or tmap.get("running")
              or tmap.get("failed") or tmap.get("cancelled")))

        # job: succeeded, counts agree
        job = await eng.meta.fetchone(
            "SELECT status, total_objects, succeeded_objects, failed_objects FROM connector_jobs "
            "WHERE connector_id=? ORDER BY started_at DESC LIMIT 1", (cid,))
        check("job: succeeded, total==succeeded, 0 failed",
              job["status"] == "succeeded" and job["total_objects"] == job["succeeded_objects"] and job["failed_objects"] == 0)

        # artifacts: the image has a cached vlm_text artifact
        art = await eng.meta.fetchone(
            "SELECT count(*) AS n FROM artifact_cache WHERE object_uri=?", (full("/pic.png"),))
        check("artifact_cache: image vlm_text artifact present", art["n"] >= 1)

        # ---- MODIFY app.py: only it changes, invariant holds ----
        await asyncio.sleep(1.05)
        open(f"{root}/app.py", "w").write("def refund(inv):\n    return gateway.refund(inv)\n")
        await eng.add(root, full=False)
        mc_app = await _mcount(eng, f'object_uri == "{_lit(full("/app.py"))}"')
        check("modify: app.py objects.chunk_count == Milvus count",
              mc_app == await _obj_count(eng, cid, "/app.py"))
        res = await eng.search("gateway refund invoice", connector_uri=cu, mode="hybrid", top_k=5)
        check("modify: search reflects new content", any("app.py" in (e["source"] or "") for e in res))

        # ---- DELETE pic.png: object + chunks + artifacts all gone, no orphan ----
        os.remove(f"{root}/pic.png")
        await eng.add(root, full=False)
        check("delete: object row gone", await _obj_count(eng, cid, "/pic.png") is None)
        check("delete: Milvus chunks for pic.png purged", await _mcount(eng, f'object_uri == "{_lit(full("/pic.png"))}"') == 0)
        art2 = await eng.meta.fetchone("SELECT count(*) AS n FROM artifact_cache WHERE object_uri=?", (full("/pic.png"),))
        check("delete: artifact_cache row purged (no orphan)", art2["n"] == 0)

        # ---- REMOVE connector: NOTHING left anywhere ----
        await eng.remove_connector(root)
        check("remove: Milvus fully purged", await _mcount(eng) == 0)
        leftovers = {}
        # connector_id-keyed tables
        for t in ("objects", "object_tasks", "connector_jobs", "connector_state", "file_state"):
            r = await eng.meta.fetchone(f"SELECT count(*) AS n FROM {t} WHERE connector_id=?", (cid,))
            if r["n"]: leftovers[t] = r["n"]
        # connectors keyed by id
        r = await eng.meta.fetchone("SELECT count(*) AS n FROM connectors WHERE id=?", (cid,))
        if r["n"]: leftovers["connectors"] = r["n"]
        # artifact_cache keyed by object_uri -> purge by connector_uri prefix
        r = await eng.meta.fetchone("SELECT count(*) AS n FROM artifact_cache WHERE object_uri LIKE ?", (cu + "%",))
        if r["n"]: leftovers["artifact_cache"] = r["n"]
        check("remove: every backend table purged (no orphan rows)", leftovers == {})
    finally:
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        await eng.shutdown(); shutil.rmtree(root, ignore_errors=True); os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'='*46}\n  state consistency / observability: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
