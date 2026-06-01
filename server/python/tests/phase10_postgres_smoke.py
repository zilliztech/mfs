"""Phase 10 postgres connector smoke — structured connector e2e. Needs local PG
(db 'mfstest' with table tickets) + OPENAI_API_KEY (bash -ic). Lite.

add postgres -> table_rows -> per_row row_text chunks (text_fields joined) + locator
(pk) -> search returns the matching row with its locator.
"""

import asyncio
import os

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []
DSN = "postgresql:///mfstest?host=/var/run/postgresql"


def check(name, cond):
    results.append((name, cond))
    print(f"  [{OK if cond else FAIL}] {name}")
    return cond


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic")
        raise SystemExit(2)
    base = f"/tmp/mfs_p10pg_{os.getpid()}"
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
        eng.milvus.drop_collection("default")
        eng.milvus.ensure_collection("default")
        pg_config = {
            "dsn": DSN,
            "schemas": ["public"],
            "objects": [
                {
                    "match": "*rows.jsonl",
                    "text_fields": ["subject", "description"],
                    "metadata_fields": ["status"],
                    "locator_fields": ["id"],
                    "chunk_strategy": "per_row",
                }
            ],
        }
        await eng.add("postgres://test", config=pg_config)

        conn = await eng.meta.fetchone("SELECT id FROM connectors WHERE type='postgres'")
        check("postgres connector registered", conn is not None)
        o = await eng.meta.fetchone(
            "SELECT chunk_count, search_status FROM objects WHERE connector_id=? AND object_uri='/public/tickets/rows.jsonl'",
            (conn["id"],),
        )
        check(
            "tickets rows.jsonl per_row indexed (3 chunks)",
            o and o["chunk_count"] == 3 and o["search_status"] == "indexed",
        )

        res = await eng.search(
            "SSO login redirect fails for enterprise users",
            connector_uri="postgres://test",
            mode="hybrid",
            top_k=3,
        )
        check(
            "search returns a ticket row",
            len(res) > 0 and any("rows.jsonl" in (e["source"] or "") for e in res),
        )
        top = res[0] if res else {}
        check("hit chunk_kind=row_text", top.get("metadata", {}).get("chunk_kind") == "row_text")
        check(
            "hit has locator with pk id",
            isinstance(top.get("locator"), dict) and "id" in (top.get("locator") or {}),
        )
        check("top hit is the SSO ticket (id=1)", (top.get("locator") or {}).get("id") == 1)

        # keyword search on a distinct row
        res2 = await eng.search(
            "dark theme toggle", connector_uri="postgres://test", mode="hybrid", top_k=3
        )
        check(
            "search 'dark mode' finds id=2",
            any((e.get("locator") or {}).get("id") == 2 for e in res2[:2]),
        )
    finally:
        try:
            eng.milvus.drop_collection("default")
        except Exception:
            pass
        await eng.shutdown()
        os.system(f"rm -rf '{base}'*")

    passed = sum(1 for _, c in results if c)
    total = len(results)
    print(f"\n{'=' * 40}\nPhase 10 postgres: {passed}/{total} checks passed")
    if passed != total:
        print("FAILED:", [n for n, c in results if not c])
        raise SystemExit(1)
    print("ALL PASS")


if __name__ == "__main__":
    asyncio.run(main())
