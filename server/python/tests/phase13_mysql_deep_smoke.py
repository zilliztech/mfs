"""Phase 13 — mysql connector DEEP coverage (schema_summary, cat --locator, NULL/json,
incremental modify/delete). Creates + drops its own table in the live mfstest DB.
Needs OPENAI_API_KEY + local MySQL/MariaDB (127.0.0.1:3306 mfs/mfs/mfstest). Lite.
"""
import asyncio
import json as _json
import os

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []
MY = dict(host="127.0.0.1", port=3306, user="mfs", password="mfs", db="mfstest")


def check(name, cond):
    results.append(bool(cond)); print(f"  [{OK if cond else FAIL}] {name}"); return cond


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic"); raise SystemExit(2)
    import aiomysql
    try:
        conn = await aiomysql.connect(autocommit=True, **MY)
    except Exception as e:  # noqa: BLE001
        print(f"mysql not reachable: {e}"); raise SystemExit(2)
    tbl = f"deep_{os.getpid()}"
    cur = await conn.cursor()
    await cur.execute(f"DROP TABLE IF EXISTS `{tbl}`")
    await cur.execute(f"""CREATE TABLE `{tbl}` (
        id INT PRIMARY KEY, subject TEXT, body TEXT, meta JSON,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP)""")
    await cur.executemany(
        f"INSERT INTO `{tbl}` (id, subject, body, meta) VALUES (%s,%s,%s,%s)",
        [(9001, "SSO login fails", "Users bounced back to the identity provider after login", '{"sev":1}'),
         (9002, "Dark mode toggle", "Add a dark theme switch to the settings page", '{"sev":3}'),
         (9003, "Null body row", None, None)])

    base = f"/tmp/mfs_myd_{os.getpid()}"; os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"; cfg.milvus.uri = base + "_v.db"; cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"; cfg.transformation_cache.db_path = base + "_t.db"
    cfg.summary.enabled = True
    eng = Engine(cfg)
    await eng.startup()
    os.environ["MFS_TEST_MYSQL_PW"] = "mfs"     # password via credential_ref so reopen works
    cfg_obj = {"host": "127.0.0.1", "port": 3306, "user": "mfs", "database": "mfstest",
               "credential_ref": "env:MFS_TEST_MYSQL_PW",
               "objects": [{"match": "*rows.jsonl", "text_fields": ["subject", "body"],
                            "locator_fields": ["id"], "chunk_strategy": "per_row"}]}
    rows_uri = f"/{tbl}/rows.jsonl"; src = "mysql://deep" + rows_uri; conn_uri = "mysql://deep"
    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")
        await eng.add("mysql://deep", config=cfg_obj)

        cid = (await eng.meta.fetchone("SELECT id FROM connectors WHERE type='mysql'"))["id"]
        ro = await eng.meta.fetchone(
            "SELECT chunk_count FROM objects WHERE connector_id=? AND object_uri=?", (cid, rows_uri))
        check("mysql rows indexed (incl NULL row)", ro and ro["chunk_count"] >= 3)
        sc = await asyncio.to_thread(
            eng.milvus.get_chunks_by_object, "default", conn_uri, conn_uri + f"/{tbl}/schema.json")
        check("mysql schema_summary produced", any(c.get("chunk_kind") == "schema_summary" for c in sc))

        res = await eng.search("single sign-on bounced identity provider", connector_uri=conn_uri, mode="hybrid", top_k=5)
        hit = next((e for e in res if e.get("locator")), None)
        check("mysql search hit carries {id} locator", hit and "id" in (hit.get("locator") or {}))
        catout = await eng.cat(src, locator={"id": 9001})
        recd = _json.loads(catout["content"])
        check("mysql cat --locator exact record", recd.get("id") == 9001 and "identity provider" in (recd.get("body") or ""))
        rn = await eng.search("null body row", connector_uri=conn_uri, mode="hybrid", top_k=5)
        check("mysql NULL-column row searchable", any((e.get("locator") or {}).get("id") == 9003 for e in rn))

        # incremental modify + delete
        await cur.execute(f"UPDATE `{tbl}` SET body=%s WHERE id=9002", ("Now supports a high-contrast accessibility palette",))
        await eng.add("mysql://deep", config=cfg_obj, full=False)
        rm = await eng.search("high-contrast accessibility palette", connector_uri=conn_uri, mode="hybrid", top_k=5)
        check("mysql modify reflected", any((e.get("locator") or {}).get("id") == 9002 for e in rm))
        await cur.execute(f"DELETE FROM `{tbl}` WHERE id=9001")
        await eng.add("mysql://deep", config=cfg_obj, full=False)
        rd = await eng.search("single sign-on bounced identity provider", connector_uri=conn_uri, mode="hybrid", top_k=10)
        check("mysql deleted row gone", not any((e.get("locator") or {}).get("id") == 9001 for e in rd))
    finally:
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        await eng.shutdown()
        await cur.execute(f"DROP TABLE IF EXISTS `{tbl}`"); await cur.close(); conn.close()
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'='*46}\n  mysql deep: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
