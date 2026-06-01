"""Phase 6 web connector smoke — crawl a public page -> md -> index -> search.
Needs OPENAI_API_KEY + network (bash -ic). Crawls example.com (stable, IANA-maintained).
Lite backend (web is backend-agnostic; Phase 8 matrix covers Zilliz).
"""

import asyncio
import os

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
    base = f"/tmp/mfs_p6web_{os.getpid()}"
    os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_meta.db"
    cfg.milvus.uri = base + "_milvus.db"
    cfg.milvus.token = ""
    cfg.object_store.root = base + "_cache"
    cfg.transformation_cache.db_path = base + "_tx.db"
    eng = Engine(cfg)
    await eng.startup()
    try:
        eng.milvus.drop_collection(cfg.namespace)
        eng.milvus.ensure_collection(cfg.namespace)

        job = await eng.add(
            "web://example",
            config={
                "start_urls": ["https://example.com"],
                "allowed_domains": ["example.com"],
                "max_pages": 5,
            },
        )
        conn = await eng.meta.fetchone("SELECT id FROM connectors WHERE type='web'")
        check("web connector registered", conn is not None)
        objs = await eng.meta.fetchall(
            "SELECT object_uri, chunk_count, search_status FROM objects WHERE connector_id=?",
            (conn["id"],),
        )
        page = next((o for o in objs if o["object_uri"].endswith(".md")), None)
        check(
            "crawled page indexed with chunks",
            page is not None and page["chunk_count"] > 0 and page["search_status"] == "indexed",
        )

        res = await eng.search(
            "domain for use in illustrative examples in documents",
            connector_uri="web://example",
            mode="hybrid",
            top_k=3,
        )
        check(
            "search returns crawled page",
            len(res) > 0 and any("example.com" in (e["source"] or "") for e in res),
        )

        # re-add: ETag 304 should skip unchanged page -> 0 new tasks (or unchanged)
        job2 = await eng.add("web://example")
        t2 = await eng.meta.fetchall(
            "SELECT change_kind FROM object_tasks WHERE connector_job_id=?", (job2,)
        )
        check(
            "re-crawl: ETag/304 skips unchanged (0 tasks) or re-modified",
            len(t2) == 0 or all(t["change_kind"] == "modified" for t in t2),
        )
    finally:
        try:
            eng.milvus.drop_collection(cfg.namespace)
        except Exception:
            pass
        await eng.shutdown()
        os.system(f"rm -rf '{base}'*")

    passed = sum(1 for _, c in results if c)
    total = len(results)
    print(f"\n{'=' * 40}\nPhase 6 web: {passed}/{total} checks passed")
    if passed != total:
        print("FAILED:", [n for n, c in results if not c])
        raise SystemExit(1)
    print("ALL PASS")


if __name__ == "__main__":
    asyncio.run(main())
