"""Phase 13 — BigQuery connector live e2e via the goccy/bigquery-emulator (matrix A12/B-bq).

Seeds a table in the emulator, indexes it through the bigquery connector (table_rows +
schema_summary), searches, and reopens a row by locator. Exercises the new `endpoint`
(self-hosted/emulator) support. Needs OPENAI_API_KEY + the mfs-bq emulator on :9050. Lite.
"""

import asyncio
import json as _json
import os
import socket

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []
EP = "http://127.0.0.1:9050"
PROJECT = "mfstest"
DS = "tickets_ds"


def check(name, cond):
    results.append(bool(cond))
    print(f"  [{OK if cond else FAIL}] {name}")
    return cond


def _up():
    s = socket.socket()
    s.settimeout(2)
    try:
        return s.connect_ex(("127.0.0.1", 9050)) == 0
    finally:
        s.close()


def _bq_client():
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery

    return bigquery.Client(
        project=PROJECT,
        client_options=ClientOptions(api_endpoint=EP),
        credentials=AnonymousCredentials(),
    )


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic")
        raise SystemExit(2)
    if not _up():
        print("bigquery emulator not reachable on :9050 — start mfs-bq")
        raise SystemExit(2)

    tbl = "tickets"
    c = _bq_client()
    from google.cloud import bigquery as bq

    ref = bq.TableReference.from_string(f"{PROJECT}.{DS}.{tbl}")
    # seed via job-free table APIs (the emulator hangs on query-job polling)
    await asyncio.to_thread(lambda: c.delete_table(ref, not_found_ok=True))
    schema = [
        bq.SchemaField("id", "INT64"),
        bq.SchemaField("subject", "STRING"),
        bq.SchemaField("body", "STRING"),
    ]
    await asyncio.to_thread(lambda: c.create_table(bq.Table(ref, schema=schema)))
    errs = await asyncio.to_thread(
        lambda: c.insert_rows_json(
            ref,
            [
                {
                    "id": 1,
                    "subject": "SSO login fails",
                    "body": "Users bounced to the identity provider after login",
                },
                {
                    "id": 2,
                    "subject": "Dark mode toggle",
                    "body": "Add a dark theme switch to the settings page",
                },
                {
                    "id": 3,
                    "subject": "CSV export truncated",
                    "body": "Large report export is cut at 65000 rows",
                },
            ],
        )
    )
    check("seeded bigquery table via job-free API", errs == [])

    base = f"/tmp/mfs_bq_{os.getpid()}"
    os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"
    cfg.milvus.uri = base + "_v.db"
    cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"
    cfg.transformation_cache.db_path = base + "_t.db"
    cfg.summary.enabled = True
    eng = Engine(cfg)
    await eng.startup()
    cfg_obj = {
        "project": PROJECT,
        "endpoint": EP,
        "datasets": [DS],
        "objects": [
            {
                "match": "*rows.jsonl",
                "text_fields": ["subject", "body"],
                "locator_fields": ["id"],
                "chunk_strategy": "per_row",
            }
        ],
    }
    conn_uri = "bigquery://dev"
    rows_uri = f"/{DS}/tables/{tbl}/rows.jsonl"
    src = conn_uri + rows_uri
    try:
        eng.milvus.drop_collection("default")
        eng.milvus.ensure_collection("default")
        await eng.add("bigquery://dev", config=cfg_obj)

        cid = (await eng.meta.fetchone("SELECT id FROM connectors WHERE type='bigquery'"))["id"]
        ro = await eng.meta.fetchone(
            "SELECT chunk_count FROM objects WHERE connector_id=? AND object_uri=?", (cid, rows_uri)
        )
        check("bigquery rows indexed (3)", ro and ro["chunk_count"] >= 3)
        sc = await asyncio.to_thread(
            eng.milvus.get_chunks_by_object,
            "default",
            conn_uri,
            conn_uri + f"/{DS}/tables/{tbl}/schema.json",
        )
        check(
            "bigquery schema_summary produced",
            any(x.get("chunk_kind") == "schema_summary" for x in sc),
        )

        res = await eng.search(
            "single sign-on identity provider login", connector_uri=conn_uri, mode="hybrid", top_k=5
        )
        hit = next((e for e in res if e.get("locator")), None)
        check(
            "bigquery search hit carries {id} locator", hit and "id" in (hit.get("locator") or {})
        )
        catout = await eng.cat(src, locator={"id": 1})
        recd = _json.loads(catout["content"])
        check(
            "bigquery cat --locator exact record",
            recd.get("id") == 1 and "identity provider" in (recd.get("body") or ""),
        )
    finally:
        try:
            eng.milvus.drop_collection("default")
        except Exception:
            pass
        await eng.shutdown()
        try:
            await asyncio.to_thread(lambda: c.delete_table(ref, not_found_ok=True))
        except Exception:
            pass
        c.close()
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'=' * 46}\n  bigquery emulator e2e: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
