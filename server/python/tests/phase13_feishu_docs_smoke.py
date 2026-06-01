"""Phase 13 — feishu docs live e2e via tenant token + explicit extra_docs.

Drives the engine through the real Feishu Open API (tenant token, lark-oapi SDK) using
ONE explicit doc token the user shared with the bot. Verifies the new /docs/ subtree
end-to-end: enumerate -> index -> cat -> search.

Env: FEISHU_APP_ID, FEISHU_APP_SECRET (+ OPENAI_API_KEY via bash -ic).
Hard-coded DOC_TOKEN below — that's the doc the user gave us. The doc must be shared
with the bot via "..." -> "添加协作者" -> select the app.
"""

import asyncio
import os

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []

# the test doc the user provided — title "别被 1M 上下文骗了…", 8.8KB body
DOC_TOKEN = "ZsnVdP2IaoJei1xpIqScnZ64nqg"


def check(name, cond):
    results.append(bool(cond))
    print(f"  [{OK if cond else FAIL}] {name}")
    return cond


async def main():
    for v in ("OPENAI_API_KEY", "FEISHU_APP_ID", "FEISHU_APP_SECRET"):
        if not os.environ.get(v):
            print(f"{v} not set — run via bash -ic")
            raise SystemExit(2)

    base = f"/tmp/mfs_fs_docs_{os.getpid()}"
    os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"
    cfg.milvus.uri = base + "_v.db"
    cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"
    cfg.transformation_cache.db_path = base + "_t.db"
    eng = Engine(cfg)
    await eng.startup()

    conn_uri = "feishu://docs-e2e"
    cfg_obj = {
        "app_id": os.environ["FEISHU_APP_ID"],
        "credential_ref": "env:FEISHU_APP_SECRET",
        "extra_docs": [{"token": DOC_TOKEN, "label": "1M-context-claude-code"}],
    }
    try:
        eng.milvus.drop_collection("default")
        eng.milvus.ensure_collection("default")
        await eng.add(conn_uri, config=cfg_obj)

        # 1) connector registered
        crow = await eng.meta.fetchone("SELECT id FROM connectors WHERE type='feishu'")
        check("feishu connector registered", crow is not None)
        cid = crow["id"]

        # 2) the docs subtree carries our one extra_doc
        all_objs = await eng.meta.fetchall(
            "SELECT object_uri, chunk_count, search_status FROM objects WHERE connector_id=? "
            "ORDER BY object_uri",
            (cid,),
        )
        doc_objs = [r for r in all_objs if r["object_uri"].startswith("/docs/")]
        check(
            f"docs subtree contains the extra_doc (got {len(doc_objs)} doc paths)",
            len(doc_objs) == 1,
        )

        # 3) doc indexed with at least 1 chunk
        if doc_objs:
            d = doc_objs[0]
            check(
                f"doc indexed with >=1 chunk (got {d['chunk_count']}, status={d['search_status']!r})",
                (d["chunk_count"] or 0) >= 1,
            )

            # 4) cat returns the doc body
            full_uri = conn_uri + d["object_uri"]
            content = await eng.cat(full_uri)
            check(
                f"cat returns the markdown body ({len(content) if isinstance(content, str) else '?'} chars)",
                isinstance(content, str) and len(content) > 100,
            )
            check(
                "cat content includes a known phrase from the doc",
                isinstance(content, str) and "Claude Code" in content,
            )

            # 5) search hits the doc on a content-derived term
            res = await eng.search(
                "1M context Claude Code DeepSeek", connector_uri=conn_uri, mode="hybrid", top_k=5
            )
            on_our_conn = [r for r in res if (r.get("source") or "").startswith(conn_uri)]
            check(
                f"search returns at least 1 hit on this feishu connector "
                f"(got {len(on_our_conn)} hits total)",
                len(on_our_conn) >= 1,
            )
    finally:
        try:
            eng.milvus.drop_collection("default")
        except Exception:
            pass
        await eng.shutdown()
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'=' * 46}\n  feishu docs live: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
