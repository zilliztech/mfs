"""Phase 13 — MongoDB connector live e2e (matrix R6.4 / A13).

Seeds a Mongo collection, indexes it through the mongo connector (record_collection ->
row_text/record_aggregate), then searches and reads back a record by locator. Needs a
running Mongo at 127.0.0.1:27017 (the mfs-mongo docker container) + OPENAI_API_KEY. Lite.
"""
import asyncio
import os
import socket

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append(bool(cond)); print(f"  [{OK if cond else FAIL}] {name}"); return cond


def _mongo_up():
    s = socket.socket(); s.settimeout(2)
    try:
        return s.connect_ex(("127.0.0.1", 27017)) == 0
    finally:
        s.close()


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic"); raise SystemExit(2)
    if not _mongo_up():
        print("mongo not reachable at 127.0.0.1:27017 — start the mfs-mongo container"); raise SystemExit(2)

    from pymongo import MongoClient
    dbname = f"mfstest_{os.getpid()}"
    mc = MongoClient("mongodb://127.0.0.1:27017")
    db = mc[dbname]
    db["tickets"].insert_many([
        {"ticket_id": 1, "title": "Payment fails on checkout", "body": "Stripe webhook returns 402 intermittently", "status": "open"},
        {"ticket_id": 2, "title": "Login loop after SSO", "body": "Session token not persisted, user bounced to IdP", "status": "open"},
        {"ticket_id": 3, "title": "Export to CSV truncates", "body": "Large report cut at 65k rows", "status": "closed"},
    ])

    base = f"/tmp/mfs_mongo_{os.getpid()}"; os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"; cfg.milvus.uri = base + "_v.db"; cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"; cfg.transformation_cache.db_path = base + "_t.db"
    cfg.summary.enabled = False
    eng = Engine(cfg)
    await eng.startup()
    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")
        mongo_config = {
            "uri": "mongodb://127.0.0.1:27017",
            "database": dbname,
            "objects": [{
                "match": "*documents.jsonl",
                "text_fields": ["title", "body"],
                "metadata_fields": ["status"],
                "locator_fields": ["ticket_id"],
                "chunk_strategy": "per_row",
            }],
        }
        await eng.add("mongo://test", config=mongo_config)

        conn = await eng.meta.fetchone("SELECT id FROM connectors WHERE type='mongo'")
        check("mongo connector registered", conn is not None)
        docobj = await eng.meta.fetchone(
            "SELECT chunk_count, search_status FROM objects WHERE connector_id=? AND object_uri='/tickets/documents.jsonl'",
            (conn["id"],))
        check("tickets documents.jsonl indexed", docobj and docobj["chunk_count"] >= 1)

        res = await eng.search("payment checkout stripe webhook fails", mode="hybrid", top_k=5)
        check("mongo records searchable", any("tickets" in (e["source"] or "") for e in res))
        hit = next((e for e in res if "tickets" in (e["source"] or "")), None)
        check("record carries a locator", hit is not None and hit.get("locator") is not None)
    finally:
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        await eng.shutdown()
        try: mc.drop_database(dbname)
        except Exception: pass
        mc.close(); os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'='*46}\n  mongo live e2e: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
