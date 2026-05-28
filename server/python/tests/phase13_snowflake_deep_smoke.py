"""Phase 13 — snowflake connector live e2e (key-pair JWT auth, schema_summary,
cat --locator, NULL row, incremental modify/delete). Creates its own warehouse +
database + schema + table on the configured account, drops everything in finally.
Needs OPENAI_API_KEY + a Snowflake account whose user has an RSA_PUBLIC_KEY set
(matching the private key at SNOWFLAKE_PK_FILE). Lite-ish — costs a few seconds of
XSMALL warehouse credit.

Env: SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PK_FILE (path to PEM PKCS#8 priv key).
"""
import asyncio
import json as _json
import os

from cryptography.hazmat.primitives import serialization
import snowflake.connector as sc

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append(bool(cond)); print(f"  [{OK if cond else FAIL}] {name}"); return cond


def _direct_conn(account, user, pk_path):
    """Direct snowflake connection (key-pair) for DDL/DML setup + teardown outside MFS."""
    with open(pk_path, "rb") as f:
        pk_obj = serialization.load_pem_private_key(f.read(), password=None)
    pk_der = pk_obj.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption())
    return sc.connect(account=account, user=user, authenticator="SNOWFLAKE_JWT",
                      private_key=pk_der, login_timeout=30)


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic"); raise SystemExit(2)
    acct = os.environ.get("SNOWFLAKE_ACCOUNT", "PQELFFW-OE99512")
    user = os.environ.get("SNOWFLAKE_USER", "ZC277584121")
    pk_file = os.environ.get("SNOWFLAKE_PK_FILE", "/home/zhangchen/.snowflake/snowflake_mfs.p8")
    if not os.path.exists(pk_file):
        print(f"private key not found: {pk_file}"); raise SystemExit(2)

    # naming: unique-per-PID so parallel runs / leftover state don't collide
    wh = f"MFS_E2E_WH_{os.getpid()}"
    db = f"MFS_E2E_DB_{os.getpid()}"
    sch = "PUBLIC"
    tbl = f"TICKETS_{os.getpid()}"

    # ---- Phase 1: provision warehouse + db + table directly (outside MFS) ----
    try:
        sf = await asyncio.to_thread(_direct_conn, acct, user, pk_file)
    except Exception as e:
        print(f"snowflake unreachable: {type(e).__name__}: {str(e)[:200]}"); raise SystemExit(2)
    cur = sf.cursor()

    def exec_(sql, params=None):
        cur.execute(sql, params) if params else cur.execute(sql)

    await asyncio.to_thread(exec_,
        f"CREATE OR REPLACE WAREHOUSE {wh} WITH WAREHOUSE_SIZE='XSMALL' "
        "AUTO_SUSPEND=60 AUTO_RESUME=TRUE INITIALLY_SUSPENDED=FALSE")
    await asyncio.to_thread(exec_, f"CREATE OR REPLACE DATABASE {db}")
    await asyncio.to_thread(exec_, f"USE DATABASE {db}")
    await asyncio.to_thread(exec_, f"USE SCHEMA {sch}")
    await asyncio.to_thread(exec_,
        f"CREATE OR REPLACE TABLE {tbl} ("
        " ID INT PRIMARY KEY, SUBJECT STRING, BODY STRING,"
        " UPDATED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP())")
    seed = [
        (9001, "SSO login fails", "Users bounced back to the identity provider after login"),
        (9002, "Dark mode toggle", "Add a dark theme switch to the settings page"),
        (9003, "Null body row", None),
    ]
    await asyncio.to_thread(cur.executemany,
        f"INSERT INTO {tbl} (ID, SUBJECT, BODY) VALUES (%s, %s, %s)", seed)

    # ---- Phase 2: drive MFS engine with snowflake connector ----
    base = f"/tmp/mfs_sfd_{os.getpid()}"; os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"; cfg.milvus.uri = base + "_v.db"; cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"; cfg.transformation_cache.db_path = base + "_t.db"
    cfg.summary.enabled = True
    eng = Engine(cfg)
    await eng.startup()

    cfg_obj = {
        "account": acct, "user": user, "warehouse": wh, "database": db, "schema": sch,
        "role": "ACCOUNTADMIN",
        "credential_ref": f"file:{pk_file}",
        "objects": [{"match": "*rows.jsonl",
                     "text_fields": ["SUBJECT", "BODY"],  # snowflake returns UPPER col names
                     "locator_fields": ["ID"], "chunk_strategy": "per_row"}],
    }
    rows_uri = f"/{db}/{sch}/tables/{tbl}/rows.jsonl"
    src = "snowflake://e2e" + rows_uri
    conn_uri = "snowflake://e2e"

    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")
        await eng.add(conn_uri, config=cfg_obj)

        cid_row = await eng.meta.fetchone("SELECT id FROM connectors WHERE type='snowflake'")
        check("snowflake connector registered", cid_row is not None)
        cid = cid_row["id"]
        ro = await eng.meta.fetchone(
            "SELECT chunk_count FROM objects WHERE connector_id=? AND object_uri=?", (cid, rows_uri))
        check(f"rows.jsonl indexed with >=3 chunks (got {ro['chunk_count'] if ro else 'None'})",
              ro and ro["chunk_count"] >= 3)
        sc_chunks = await asyncio.to_thread(
            eng.milvus.get_chunks_by_object, "default", conn_uri,
            conn_uri + f"/{db}/{sch}/tables/{tbl}/schema.json")
        check("schema_summary produced for table schema.json",
              any(c.get("chunk_kind") == "schema_summary" for c in sc_chunks))

        res = await eng.search("single sign-on identity provider bounce",
                               connector_uri=conn_uri, mode="hybrid", top_k=5)
        hit = next((e for e in res if e.get("locator")), None)
        check("search hit carries {ID} locator",
              hit and "ID" in (hit.get("locator") or {}))
        catout = await eng.cat(src, locator={"ID": 9001})
        recd = _json.loads(catout["content"])
        check("cat --locator returns exact record id=9001",
              recd.get("ID") == 9001 and "identity provider" in (recd.get("BODY") or ""))

        rn = await eng.search("null body row", connector_uri=conn_uri, mode="hybrid", top_k=5)
        check("NULL-column row still searchable (id=9003)",
              any((e.get("locator") or {}).get("ID") == 9003 for e in rn))

        # incremental: modify -> reindex -> search reflects new text
        await asyncio.to_thread(exec_,
            f"UPDATE {tbl} SET BODY=%s, UPDATED_AT=CURRENT_TIMESTAMP() WHERE ID=9002",
            ("Now supports a high-contrast accessibility palette",))
        await eng.add(conn_uri, config=cfg_obj, full=False)
        rm = await eng.search("high-contrast accessibility palette",
                              connector_uri=conn_uri, mode="hybrid", top_k=5)
        check("modify reflected in search (id=9002 with new body)",
              any((e.get("locator") or {}).get("ID") == 9002 for e in rm))

        # incremental: delete -> reindex -> old text gone
        await asyncio.to_thread(exec_, f"DELETE FROM {tbl} WHERE ID=9001")
        await eng.add(conn_uri, config=cfg_obj, full=False)
        rd = await eng.search("single sign-on identity provider bounce",
                              connector_uri=conn_uri, mode="hybrid", top_k=10)
        check("deleted row absent (id=9001 gone)",
              not any((e.get("locator") or {}).get("ID") == 9001 for e in rd))
    finally:
        # ---- teardown: drop everything we created, even on failure ----
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        await eng.shutdown()
        try:
            await asyncio.to_thread(exec_, f"DROP TABLE IF EXISTS {db}.{sch}.{tbl}")
            await asyncio.to_thread(exec_, f"DROP DATABASE IF EXISTS {db}")
            await asyncio.to_thread(exec_, f"DROP WAREHOUSE IF EXISTS {wh}")
        finally:
            cur.close(); sf.close()
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'='*46}\n  snowflake deep: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
