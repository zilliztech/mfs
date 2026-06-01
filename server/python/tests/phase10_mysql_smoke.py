"""Phase 10 mysql connector smoke — structured connector on MariaDB (proves the
table_rows pipeline isn't postgres-specific). Needs local MariaDB (db mfstest) +
OPENAI_API_KEY (bash -ic). Lite.
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
    base = f"/tmp/mfs_p10my_{os.getpid()}"
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
        my_config = {
            "host": "127.0.0.1",
            "port": 3306,
            "user": "mfs",
            "password": "mfs",
            "database": "mfstest",
            "objects": [
                {
                    "match": "*rows.jsonl",
                    "text_fields": ["subject", "description"],
                    "locator_fields": ["id"],
                    "chunk_strategy": "per_row",
                }
            ],
        }
        await eng.add("mysql://test", config=my_config)

        conn = await eng.meta.fetchone("SELECT id FROM connectors WHERE type='mysql'")
        check("mysql connector registered", conn is not None)
        o = await eng.meta.fetchone(
            "SELECT chunk_count, search_status FROM objects WHERE connector_id=? AND object_uri='/tickets/rows.jsonl'",
            (conn["id"],),
        )
        check(
            "tickets rows.jsonl per_row indexed (3 chunks)",
            o and o["chunk_count"] == 3 and o["search_status"] == "indexed",
        )

        res = await eng.search(
            "SSO login redirect fails", connector_uri="mysql://test", mode="hybrid", top_k=3
        )
        top = res[0] if res else {}
        check(
            "search returns row_text with pk locator",
            top.get("metadata", {}).get("chunk_kind") == "row_text"
            and (top.get("locator") or {}).get("id") == 1,
        )
        res2 = await eng.search(
            "dark theme toggle setting", connector_uri="mysql://test", mode="hybrid", top_k=3
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
    print(f"\n{'=' * 40}\nPhase 10 mysql: {passed}/{total} checks passed")
    if passed != total:
        print("FAILED:", [n for n, c in results if not c])
        raise SystemExit(1)
    print("ALL PASS")


if __name__ == "__main__":
    asyncio.run(main())
