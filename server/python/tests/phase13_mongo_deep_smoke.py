"""Phase 13 — mongo connector DEEP coverage (schema_summary, cat --locator, missing
locator, incremental modify/delete). Needs running Mongo (mfs-mongo container) + OPENAI_API_KEY.
"""
import asyncio
import datetime as dt
import json as _json
import os
import socket

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append(bool(cond)); print(f"  [{OK if cond else FAIL}] {name}"); return cond


def _up():
    s = socket.socket(); s.settimeout(2)
    try: return s.connect_ex(("127.0.0.1", 27017)) == 0
    finally: s.close()


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic"); raise SystemExit(2)
    if not _up():
        print("mongo not reachable — start mfs-mongo"); raise SystemExit(2)
    from pymongo import MongoClient
    dbname = f"mfsdeep_{os.getpid()}"
    mc = MongoClient("mongodb://127.0.0.1:27017"); coll = mc[dbname]["tickets"]
    now = dt.datetime.now(dt.timezone.utc)
    coll.insert_many([
        {"ticket_id": 1, "title": "SSO login fails", "body": "Users bounced to the identity provider", "updatedAt": now},
        {"ticket_id": 2, "title": "Dark mode toggle", "body": "Add a dark theme switch", "updatedAt": now},
        {"ticket_id": 3, "title": "Null body", "body": None, "updatedAt": now}])

    base = f"/tmp/mfs_mod_{os.getpid()}"; os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"; cfg.milvus.uri = base + "_v.db"; cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"; cfg.transformation_cache.db_path = base + "_t.db"
    cfg.summary.enabled = True
    eng = Engine(cfg); await eng.startup()
    cfg_obj = {"uri": "mongodb://127.0.0.1:27017", "database": dbname, "cursor_field": "updatedAt",
               "objects": [{"match": "*documents.jsonl", "text_fields": ["title", "body"],
                            "locator_fields": ["ticket_id"], "chunk_strategy": "per_row"}]}
    conn_uri = "mongo://deep"; src = conn_uri + "/tickets/documents.jsonl"
    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")
        await eng.add("mongo://deep", config=cfg_obj)

        sc = await asyncio.to_thread(eng.milvus.get_chunks_by_object, "default", conn_uri, conn_uri + "/tickets/schema.json")
        check("mongo schema_summary produced", any(c.get("chunk_kind") == "schema_summary" for c in sc))
        res = await eng.search("single sign-on identity provider", connector_uri=conn_uri, mode="hybrid", top_k=5)
        hit = next((e for e in res if e.get("locator")), None)
        check("mongo search hit carries locator", hit and "ticket_id" in (hit.get("locator") or {}))
        catout = await eng.cat(src, locator={"ticket_id": 1})
        recd = _json.loads(catout["content"])
        check("mongo cat --locator exact record", recd.get("ticket_id") == 1 and "identity provider" in (recd.get("body") or ""))
        try:
            await eng.cat(src, locator={"ticket_id": 999})
            check("mongo missing locator raises", False)
        except Exception as ex:
            check("mongo missing locator raises", "not_found" in str(ex).lower() or "locator" in str(ex).lower())
        rn = await eng.search("null body", connector_uri=conn_uri, mode="hybrid", top_k=5)
        check("mongo NULL-field doc searchable", any((e.get("locator") or {}).get("ticket_id") == 3 for e in rn))

        # incremental modify (bump cursor) + delete
        coll.update_one({"ticket_id": 2}, {"$set": {"body": "Now supports a high-contrast accessibility palette",
                                                    "updatedAt": dt.datetime.now(dt.timezone.utc)}})
        await eng.add("mongo://deep", config=cfg_obj, full=False)
        rm = await eng.search("high-contrast accessibility palette", connector_uri=conn_uri, mode="hybrid", top_k=5)
        check("mongo modify reflected", any((e.get("locator") or {}).get("ticket_id") == 2 for e in rm))
        coll.delete_one({"ticket_id": 1})
        await eng.add("mongo://deep", config=cfg_obj, full=False)
        rd = await eng.search("single sign-on identity provider", connector_uri=conn_uri, mode="hybrid", top_k=10)
        check("mongo deleted doc gone", not any((e.get("locator") or {}).get("ticket_id") == 1 for e in rd))
    finally:
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        await eng.shutdown()
        try: mc.drop_database(dbname)
        except Exception: pass
        mc.close(); os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'='*46}\n  mongo deep: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
