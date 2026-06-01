"""Phase 13 — notion connector live e2e. Drives the engine through the real Notion API
(notion-client AsyncClient) and verifies register / list / cat / search end-to-end against
whatever pages the user's integration has been shared into.

Env: NOTION_TOKEN. The integration must have been added as a connection on at least one
page or database in the workspace (otherwise sync finds nothing — that's a Notion auth
quirk, not an MFS bug). Run via bash -ic so OPENAI_API_KEY is also picked up.
"""

import asyncio
import os

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append(bool(cond))
    print(f"  [{OK if cond else FAIL}] {name}")
    return cond


async def main():
    for v in ("OPENAI_API_KEY", "NOTION_TOKEN"):
        if not os.environ.get(v):
            print(f"{v} not set — run via bash -ic")
            raise SystemExit(2)

    base = f"/tmp/mfs_notion_{os.getpid()}"
    os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"
    cfg.milvus.uri = base + "_v.db"
    cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"
    cfg.transformation_cache.db_path = base + "_t.db"
    eng = Engine(cfg)
    await eng.startup()

    conn_uri = "notion://e2e"
    cfg_obj = {"credential_ref": "env:NOTION_TOKEN"}
    try:
        eng.milvus.drop_collection("default")
        eng.milvus.ensure_collection("default")
        await eng.add(conn_uri, config=cfg_obj)

        # 1) connector registered
        crow = await eng.meta.fetchone("SELECT id FROM connectors WHERE type='notion'")
        check("notion connector registered", crow is not None)
        cid = crow["id"]

        # 2) at least one page object enumerated
        page_rows = await eng.meta.fetchall(
            "SELECT object_uri, chunk_count, search_status FROM objects "
            "WHERE connector_id=? AND object_uri LIKE '/pages/%' ORDER BY object_uri",
            (cid,),
        )
        check(
            f"at least 1 page indexed (got {len(page_rows)} pages from the workspace)",
            len(page_rows) >= 1,
        )

        # 3) at least one page has chunks (non-empty page) — empty pages legitimately produce 0
        non_empty = [r for r in page_rows if (r["chunk_count"] or 0) > 0]
        check(
            f"at least 1 NON-EMPTY page has indexed chunks (got {len(non_empty)} non-empty / {len(page_rows)} total)",
            len(non_empty) >= 1,
        )

        # 4) cat a page returns markdown (eng.cat returns a plain string for documents)
        if non_empty:
            sample = non_empty[0]
            page_uri = conn_uri + sample["object_uri"]
            content = await eng.cat(page_uri)
            check(
                f"cat returns non-empty markdown for {sample['object_uri']} ({len(content) if isinstance(content, str) else '?'} chars)",
                isinstance(content, str) and len(content) > 0,
            )

            # 5) head of the same page also works (line-bounded; returns a str)
            head_out = await eng.head(page_uri, n=5)
            check(
                "head -n 5 returns text for the same page",
                isinstance(head_out, str) and len(head_out) > 0,
            )

            # 6) search hits the content of the non-empty page
            #    pick a token that's likely in the body (markdown often has common words).
            #    Use the first non-empty word of the cat'd content as a search query.
            words = [w for w in content.split() if len(w) >= 4][:1]
            if words:
                q = words[0]
                res = await eng.search(q, connector_uri=conn_uri, mode="hybrid", top_k=5)
                check(
                    f"search for content-derived term {q!r} finds at least 1 hit on this connector",
                    any(r.get("source", "").startswith(conn_uri) for r in res),
                )
            else:
                check("page had a searchable token", False)
        else:
            print("    (skipping cat/head/search checks — no non-empty page in the workspace)")
    finally:
        try:
            eng.milvus.drop_collection("default")
        except Exception:
            pass
        await eng.shutdown()
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'=' * 46}\n  notion live: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
