"""Phase 14 — snowflake connector deep e2e.

Pushes past phase13_snowflake_deep_smoke (which proves the connector connects,
JWT auth works, schema_summary lands, locator round-trips, NULL row,
incremental modify+delete). This one nails the harder configuration paths:

  · multi-table in one connector — three tables under the same warehouse +
    database + schema, each with its own [[objects]] config.
  · composite locator (REGION, USER_ID) — two-column PK that cat --locator
    can reopen by both columns.
  · metadata_fields populated — PRIORITY / ASSIGNEE land on the chunk row
    so an agent can read them off the search envelope.
  · chunk_max truncation — table with 30 rows + chunk_max=8 lands as
    partial; search inside the indexed slice still works.
  · empty table no-op — 0 rows must not crash; objects row carries
    chunk_count=0 and search_status='not_indexed'.
  · search hits surface across BOTH bigger tables.

Needs OPENAI_API_KEY + SNOWFLAKE_ACCOUNT / SNOWFLAKE_USER (defaults to the
account we've been using) + SNOWFLAKE_PK_FILE (PEM PKCS#8 private key with
matching public key set on the user via RSA_PUBLIC_KEY). Provisions its own
warehouse + database + tables under a per-PID name and drops everything
in finally."""
import asyncio
import json as _json
import os

import snowflake.connector as sc
from cryptography.hazmat.primitives import serialization

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append(bool(cond)); print(f"  [{OK if cond else FAIL}] {name}"); return cond


def _direct_conn(account, user, pk_path):
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
    pk_file = os.environ.get("SNOWFLAKE_PK_FILE",
                              "/home/zhangchen/.snowflake/snowflake_mfs.p8")
    if not os.path.exists(pk_file):
        print(f"private key not found: {pk_file}"); raise SystemExit(2)

    wh = f"MFS_T14_WH_{os.getpid()}"
    db = f"MFS_T14_DB_{os.getpid()}"
    sch = "PUBLIC"
    tickets_t = f"TICKETS_{os.getpid()}"
    compkey_t = f"COMPKEY_{os.getpid()}"
    big_t     = f"BIG_{os.getpid()}"
    empty_t   = f"EMPTY_{os.getpid()}"

    try:
        sf = await asyncio.to_thread(_direct_conn, acct, user, pk_file)
    except Exception as e:  # noqa: BLE001
        print(f"snowflake unreachable: {type(e).__name__}: {str(e)[:200]}")
        raise SystemExit(2)
    cur = sf.cursor()

    def exec_(sql, params=None):
        cur.execute(sql, params) if params else cur.execute(sql)

    # Provision
    await asyncio.to_thread(exec_,
        f"CREATE OR REPLACE WAREHOUSE {wh} WITH WAREHOUSE_SIZE='XSMALL' "
        "AUTO_SUSPEND=60 AUTO_RESUME=TRUE INITIALLY_SUSPENDED=FALSE")
    await asyncio.to_thread(exec_, f"CREATE OR REPLACE DATABASE {db}")
    await asyncio.to_thread(exec_, f"USE DATABASE {db}")
    await asyncio.to_thread(exec_, f"USE SCHEMA {sch}")

    # tickets — text + metadata
    await asyncio.to_thread(exec_,
        f"CREATE OR REPLACE TABLE {tickets_t} ("
        " ID INT PRIMARY KEY, SUBJECT STRING, BODY STRING,"
        " PRIORITY STRING, ASSIGNEE STRING,"
        " UPDATED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP())")
    await asyncio.to_thread(cur.executemany,
        f"INSERT INTO {tickets_t} (ID, SUBJECT, BODY, PRIORITY, ASSIGNEE) "
        f"VALUES (%s, %s, %s, %s, %s)",
        [(1, "saml sso login loop",
          "After identity provider redirect the session token fails to persist",
          "high", "alice"),
         (2, "csv export truncated",
          "Large report export is cut at 65000 rows",
          "medium", "bob"),
         (3, "stripe webhook 402",
          "Intermittent 402 from Stripe webhook on payment capture",
          "high", "alice")])

    # compkey — composite PK
    await asyncio.to_thread(exec_,
        f"CREATE OR REPLACE TABLE {compkey_t} ("
        " REGION STRING, USER_ID INT, NOTE STRING,"
        " PRIMARY KEY (REGION, USER_ID))")
    await asyncio.to_thread(cur.executemany,
        f"INSERT INTO {compkey_t} (REGION, USER_ID, NOTE) VALUES (%s, %s, %s)",
        [("us-east-1", 100, "primary instance failed over to replica"),
         ("eu-west-1", 100, "noisy neighbor crowding shared volume"),
         ("us-east-1", 200, "credential rotated successfully via vault")])

    # big — 30 rows for chunk_max truncation
    await asyncio.to_thread(exec_,
        f"CREATE OR REPLACE TABLE {big_t} (ID INT PRIMARY KEY, BODY STRING)")
    await asyncio.to_thread(cur.executemany,
        f"INSERT INTO {big_t} (ID, BODY) VALUES (%s, %s)",
        [(i, f"row {i} concerns topic-{i % 5} for snowflake indexing")
         for i in range(1, 31)])

    # empty — 0 rows
    await asyncio.to_thread(exec_,
        f"CREATE OR REPLACE TABLE {empty_t} (ID INT PRIMARY KEY, BODY STRING)")

    base = f"/tmp/mfs_sfd14_{os.getpid()}"; os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"; cfg.milvus.uri = base + "_v.db"; cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"; cfg.transformation_cache.db_path = base + "_t.db"
    cfg.summary.enabled = False
    eng = Engine(cfg)
    await eng.startup()

    cfg_obj = {
        "account": acct, "user": user, "warehouse": wh, "database": db,
        "schema": sch, "role": "ACCOUNTADMIN",
        "credential_ref": f"file:{pk_file}",
        "objects": [
            {"match": f"/{db}/{sch}/tables/{tickets_t}/rows.jsonl",
             "text_fields": ["SUBJECT", "BODY"],
             "locator_fields": ["ID"],
             "metadata_fields": ["PRIORITY", "ASSIGNEE"]},
            {"match": f"/{db}/{sch}/tables/{compkey_t}/rows.jsonl",
             "text_fields": ["NOTE"],
             "locator_fields": ["REGION", "USER_ID"]},
            {"match": f"/{db}/{sch}/tables/{big_t}/rows.jsonl",
             "text_fields": ["BODY"], "locator_fields": ["ID"],
             "chunk_max": 8},
            {"match": f"/{db}/{sch}/tables/{empty_t}/rows.jsonl",
             "text_fields": ["BODY"], "locator_fields": ["ID"]},
        ],
    }
    conn_uri = "snowflake://t14"

    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")
        await eng.add(conn_uri, config=cfg_obj)
        cid = (await eng.meta.fetchone(
            "SELECT id FROM connectors WHERE root_uri=?", (conn_uri,)))["id"]
        objs = await eng.meta.fetchall(
            "SELECT object_uri, chunk_count, search_status FROM objects "
            "WHERE connector_id=?", (cid,))
        uris = {o["object_uri"]: o for o in objs}

        # ----- T1: multi-table enumeration -----
        print("\n--- T1 · multi-table enumeration ---")
        for tname in (tickets_t, compkey_t, big_t, empty_t):
            rows_path = f"/{db}/{sch}/tables/{tname}/rows.jsonl"
            check(f"T1 {tname}/rows.jsonl present",
                  rows_path in uris)
        check(f"T1 tickets indexed (chunks={uris.get(f'/{db}/{sch}/tables/{tickets_t}/rows.jsonl', {}).get('chunk_count')})",
              uris.get(f"/{db}/{sch}/tables/{tickets_t}/rows.jsonl", {}).get("chunk_count") == 3)

        # ----- T2: composite locator round-trip -----
        print("\n--- T2 · composite locator ---")
        ck_uri = conn_uri + f"/{db}/{sch}/tables/{compkey_t}/rows.jsonl"
        ck_chunks = await asyncio.to_thread(
            eng.milvus.get_chunks_by_object, "default", conn_uri, ck_uri)
        sample = next(c for c in ck_chunks
                       if "credential rotated" in (c.get("content") or ""))
        loc = sample.get("locator") or {}
        check(f"T2 composite locator carries BOTH PK columns (got {loc})",
              set(loc) >= {"REGION", "USER_ID"})
        cat_res = await eng.cat(ck_uri, locator={"REGION": "eu-west-1", "USER_ID": 100})
        recd = _json.loads(cat_res["content"])
        check("T2 cat --locator composite key reopens the right row",
              recd.get("REGION") == "eu-west-1" and recd.get("USER_ID") == 100
              and "noisy neighbor" in (recd.get("NOTE") or ""))

        # ----- T3: metadata_fields populated -----
        print("\n--- T3 · metadata_fields populated on tickets ---")
        tk_uri = conn_uri + f"/{db}/{sch}/tables/{tickets_t}/rows.jsonl"
        tk_chunks = await asyncio.to_thread(
            eng.milvus.get_chunks_by_object, "default", conn_uri, tk_uri)
        priorities = {(c.get("metadata") or {}).get("PRIORITY") for c in tk_chunks}
        assignees = {(c.get("metadata") or {}).get("ASSIGNEE") for c in tk_chunks}
        check(f"T3 PRIORITY metadata present on every chunk (got {priorities})",
              priorities == {"high", "medium"})
        check(f"T3 ASSIGNEE metadata present on every chunk (got {assignees})",
              assignees == {"alice", "bob"})

        # ----- T4: chunk_max truncation -----
        print("\n--- T4 · chunk_max=8 truncates the 30-row big table ---")
        big_ro = uris.get(f"/{db}/{sch}/tables/{big_t}/rows.jsonl") or {}
        check(f"T4 chunk_max=8 caps chunk_count (got {big_ro.get('chunk_count')})",
              big_ro.get("chunk_count") == 8)
        check(f"T4 search_status='partial' (got {big_ro.get('search_status')!r})",
              big_ro.get("search_status") == "partial")
        big_uri = conn_uri + f"/{db}/{sch}/tables/{big_t}/rows.jsonl"
        partial_hits = await eng.search(
            "topic-3", connector_uri=conn_uri, object_prefix=big_uri,
            mode="hybrid", top_k=5)
        check(f"T4 'partial' slice is searchable ({len(partial_hits)} hits)",
              len(partial_hits) >= 1)

        # ----- T5: empty table no-op -----
        print("\n--- T5 · empty table no-op ---")
        empty_ro = uris.get(f"/{db}/{sch}/tables/{empty_t}/rows.jsonl") or {}
        check(f"T5 empty table: chunks=0, status='not_indexed', no crash "
              f"(chunks={empty_ro.get('chunk_count')}, "
              f"status={empty_ro.get('search_status')!r})",
              empty_ro.get("chunk_count") == 0
              and empty_ro.get("search_status") == "not_indexed")

        # ----- T6: search hits surface across multiple tables -----
        print("\n--- T6 · search hits across tickets + compkey ---")
        tickets_res = await eng.search("saml identity provider",
                                        connector_uri=conn_uri, mode="hybrid",
                                        top_k=5)
        ck_res = await eng.search("noisy neighbor shared volume",
                                   connector_uri=conn_uri, mode="hybrid",
                                   top_k=5)
        check(f"T6 search hit lands in tickets ({len(tickets_res)} hits)",
              any(tickets_t in (h.get("source") or "")
                  for h in tickets_res))
        check(f"T6 search hit lands in compkey ({len(ck_res)} hits)",
              any(compkey_t in (h.get("source") or "")
                  for h in ck_res))

    finally:
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        await eng.shutdown()
        # Teardown — drop everything we provisioned
        for t in (tickets_t, compkey_t, big_t, empty_t):
            try: await asyncio.to_thread(exec_, f"DROP TABLE IF EXISTS {db}.{sch}.{t}")
            except Exception: pass
        try: await asyncio.to_thread(exec_, f"DROP DATABASE IF EXISTS {db}")
        except Exception: pass
        try: await asyncio.to_thread(exec_, f"DROP WAREHOUSE IF EXISTS {wh}")
        except Exception: pass
        cur.close(); sf.close()
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'='*46}\n  snowflake deep e2e: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
