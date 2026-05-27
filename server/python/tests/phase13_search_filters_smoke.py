"""Phase 13 — search filters (matrix C4 / C5 / C6 / C7).

  C4 --kind body              -> only body chunks
  C5 --kind directory_summary -> only directory summaries
  C6 collapse                 -> at most one hit per object
  C7 scoped prefix with '_'   -> byte-range scope, '/my_dir' must NOT match '/myXdir'
Also asserts the HTTP ?kind= wiring. Needs OPENAI_API_KEY. Lite.
"""
import asyncio
import os
import shutil
import tempfile

from fastapi.testclient import TestClient

from mfs_server.api.app import create_app
from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append(bool(cond)); print(f"  [{OK if cond else FAIL}] {name}"); return cond


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic"); raise SystemExit(2)
    root = tempfile.mkdtemp(prefix="mfs_filt_")
    os.makedirs(f"{root}/my_dir", exist_ok=True); os.makedirs(f"{root}/myXdir", exist_ok=True)
    # a long doc so it splits into multiple body chunks (for collapse)
    big = "# Storage\n\n" + ("This paragraph describes the storage and caching layer in detail. " * 60 + "\n\n") * 8
    open(f"{root}/my_dir/keep.md", "w").write(big)
    open(f"{root}/myXdir/skip.md", "w").write("# Other\n\nStorage and caching in the sibling directory.\n")
    base = f"/tmp/mfs_filt_{os.getpid()}"; os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"; cfg.milvus.uri = base + "_v.db"; cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"; cfg.transformation_cache.db_path = base + "_t.db"
    cfg.summary.enabled = True
    eng = Engine(cfg)
    await eng.startup()
    conn_uri = f"file://local{root}"
    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")
        await eng.add(root)

        # C4 — only body
        r = await eng.search("storage caching layer", connector_uri=conn_uri, mode="hybrid",
                             top_k=10, chunk_kinds=["body"])
        kinds = {e.get("metadata", {}).get("chunk_kind") for e in r}
        check("C4 --kind body: only body chunks", r and kinds == {"body"})

        # C5 — only directory_summary
        r = await eng.search("what does this directory contain", connector_uri=conn_uri, mode="hybrid",
                             top_k=10, chunk_kinds=["directory_summary"])
        kinds = {e.get("metadata", {}).get("chunk_kind") for e in r}
        check("C5 --kind directory_summary: only summaries", r and kinds == {"directory_summary"})

        # C6 — collapse dedups multi-chunk object
        raw = await eng.search("storage caching layer", connector_uri=conn_uri, mode="hybrid",
                               top_k=10, chunk_kinds=["body"], collapse=False)
        col = await eng.search("storage caching layer", connector_uri=conn_uri, mode="hybrid",
                               top_k=10, chunk_kinds=["body"], collapse=True)
        srcs = [e["source"] for e in col]
        check("C6 collapse: one hit per object", len(srcs) == len(set(srcs)))
        check("C6 collapse: keep.md split into >1 body chunk pre-collapse",
              sum(1 for e in raw if "keep.md" in (e["source"] or "")) > 1)

        # C7 — byte-range scope must not let '_' act as a wildcard (resolve like the HTTP layer)
        sc_uri, sc_prefix = await eng.resolve_connector_uri(f"{root}/my_dir")
        r = await eng.search("storage caching", connector_uri=sc_uri, object_prefix=sc_prefix,
                             mode="hybrid", top_k=10)
        srcs = [e["source"] or "" for e in r]
        check("C7 scope '/my_dir' matches keep.md", any("my_dir/keep.md" in s for s in srcs))
        check("C7 scope '/my_dir' excludes '/myXdir'", not any("myXdir" in s for s in srcs))

        # HTTP ?kind= wiring
        app = create_app(cfg)
        with TestClient(app) as client:
            hr = client.get("/v1/search", params={"q": "storage caching", "path": root,
                                                   "mode": "hybrid", "top_k": 10, "kind": "directory_summary"})
            hk = {x.get("metadata", {}).get("chunk_kind") for x in hr.json()["results"]}
            check("HTTP ?kind=directory_summary filters", hr.status_code == 200 and hk == {"directory_summary"})
    finally:
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        await eng.shutdown(); shutil.rmtree(root, ignore_errors=True); os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'='*46}\n  search filters: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
