"""Phase 14 — mongo connector deep e2e.

Pushes past phase13_mongo_deep_smoke:

  · multi-collection enumeration — collections 'tickets' and 'audit' in
    one database both surface as /<collection>/{documents.jsonl,schema.json}.
  · nested-field text extraction via dotted path — pymongo gives dicts
    directly (unlike asyncpg's jsonb-as-str), so text_fields=['payload.body']
    actually resolves the nested string into row_text.
  · composite locator (multi-field "primary key") — locator_fields covers
    a 2-field identity and cat --locator round-trips by both.
  · metadata_fields populated — non-text fields land in chunk metadata so a
    future filter pushdown has what to filter by.
  · chunk_max truncation — collection with N > chunk_max docs lands at
    search_status='partial'.
  · empty collection no-op — 0 documents must not crash; objects row lands
    with chunk_count=0 and search_status='not_indexed'.
  · schema_summary chunk — schema.json is summarised when summary.enabled.

Needs OPENAI_API_KEY + reachable mongodb at 127.0.0.1:27017."""

import asyncio
import json as _json
import os

from pymongo import AsyncMongoClient

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []
MONGO_URI = "mongodb://127.0.0.1:27017"
DB_NAME = "mfstest_t14"


def check(name, cond):
    results.append(bool(cond))
    print(f"  [{OK if cond else FAIL}] {name}")
    return cond


async def _drop_db(c: AsyncMongoClient):
    await c.drop_database(DB_NAME)


async def _seed(c: AsyncMongoClient) -> None:
    db = c[DB_NAME]
    # tickets — top-level text + composite identity (region, ticket_id) +
    # metadata fields (priority, assignee) + a row with nested 'payload.body'
    await db.tickets.insert_many(
        [
            {
                "region": "us-east-1",
                "ticket_id": 100,
                "subject": "saml sso login loop",
                "body": "Identity provider redirect drops the session token",
                "priority": "high",
                "assignee": "alice",
                "payload": {
                    "title": "infra",
                    "body": "cytochrome oxidase activity drops under hypoxia",
                },
                "updatedAt": "2026-05-01T10:00:00Z",
            },
            {
                "region": "us-east-1",
                "ticket_id": 101,
                "subject": "csv export truncated",
                "body": "Large report export is cut at 65000 rows",
                "priority": "medium",
                "assignee": "bob",
                "payload": {
                    "title": "ops",
                    "body": "polymer chain entanglement governs the relaxation modulus",
                },
                "updatedAt": "2026-05-02T10:00:00Z",
            },
            {
                "region": "eu-west-1",
                "ticket_id": 100,
                "subject": "noisy neighbor saturating io",
                "body": "Shared volume IO is dominated by tenant X",
                "priority": "high",
                "assignee": "alice",
                "payload": {
                    "title": "infra",
                    "body": "bgp route reflectors reduce ibgp full-mesh requirements",
                },
                "updatedAt": "2026-05-03T10:00:00Z",
            },
        ]
    )

    # audit — second collection, simple
    await db.audit.insert_many(
        [
            {
                "event_id": 1,
                "actor": "alice",
                "message": "rotated production tls certificate via vault provider",
                "updatedAt": "2026-05-01T10:00:00Z",
            },
            {
                "event_id": 2,
                "actor": "bob",
                "message": "drained replica pool ahead of kernel upgrade",
                "updatedAt": "2026-05-02T10:00:00Z",
            },
        ]
    )

    # bigcol — 50 docs to drive chunk_max truncation
    await db.bigcol.insert_many(
        [
            {
                "id": i,
                "body": f"doc {i} concerns topic-{i % 5}",
                "updatedAt": "2026-05-01T10:00:00Z",
            }
            for i in range(1, 51)
        ]
    )

    # empty — exercise the 0-doc path
    await db.create_collection("empty")


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic")
        raise SystemExit(2)

    client = AsyncMongoClient(MONGO_URI)
    try:
        await client.admin.command("ping")
    except Exception as e:  # noqa: BLE001
        print(f"mongo not reachable at {MONGO_URI}: {e}")
        raise SystemExit(2)

    await _drop_db(client)
    await _seed(client)

    base = f"/tmp/mfs_mgdeep_{os.getpid()}"
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

    os.environ["MFS_MONGO_URI_T14"] = MONGO_URI
    cfg_obj = {
        "uri": MONGO_URI,
        "database": DB_NAME,
        "objects": [
            {
                "match": "/tickets/documents.jsonl",
                "text_fields": ["subject", "body", "payload.body"],
                "locator_fields": ["region", "ticket_id"],
                "metadata_fields": ["priority", "assignee"],
            },
            {
                "match": "/audit/documents.jsonl",
                "text_fields": ["message"],
                "locator_fields": ["event_id"],
            },
            {
                "match": "/bigcol/documents.jsonl",
                "text_fields": ["body"],
                "locator_fields": ["id"],
                "chunk_max": 10,
            },
            {"match": "/empty/documents.jsonl", "text_fields": ["body"], "locator_fields": ["id"]},
        ],
    }
    conn_uri = "mongo://t14"

    try:
        eng.milvus.drop_collection("default")
        eng.milvus.ensure_collection("default")
        await eng.add(conn_uri, config=cfg_obj)
        cid = (await eng.meta.fetchone("SELECT id FROM connectors WHERE root_uri=?", (conn_uri,)))[
            "id"
        ]

        # ----- T1: multi-collection enumeration -----
        print("\n--- T1 · multi-collection enumeration ---")
        objs = await eng.meta.fetchall(
            "SELECT object_uri, chunk_count, search_status FROM objects WHERE connector_id=?",
            (cid,),
        )
        uris = {o["object_uri"]: o for o in objs}
        want = {
            "/tickets/documents.jsonl",
            "/tickets/schema.json",
            "/audit/documents.jsonl",
            "/audit/schema.json",
            "/bigcol/documents.jsonl",
            "/bigcol/schema.json",
            "/empty/documents.jsonl",
            "/empty/schema.json",
        }
        check(
            f"T1 all 8 collection objects landed (missing={sorted(want - set(uris))})",
            want <= set(uris),
        )
        check(
            f"T1 audit collection independently indexed "
            f"(chunks={uris.get('/audit/documents.jsonl', {}).get('chunk_count')})",
            uris.get("/audit/documents.jsonl", {}).get("chunk_count") == 2,
        )

        # ----- T2: nested-field text extraction via dotted path -----
        print("\n--- T2 · nested-field text via dotted path ---")
        # pymongo gives a real dict for an embedded doc, so 'payload.body'
        # resolves through _resolve_path. The chunk text should include the
        # nested string.
        tk_uri = conn_uri + "/tickets/documents.jsonl"
        tk_chunks = await asyncio.to_thread(
            eng.milvus.get_chunks_by_object, "default", conn_uri, tk_uri
        )
        joined = " | ".join((c.get("content") or "") for c in tk_chunks)
        check(
            "T2 nested 'payload.body' lands in row_text "
            "(cytochrome oxidase / polymer chain / bgp route reflectors)",
            "cytochrome oxidase" in joined
            and "polymer chain" in joined
            and "bgp route reflectors" in joined,
        )
        res_nest = await eng.search(
            "cytochrome oxidase activity hypoxia", connector_uri=conn_uri, mode="hybrid", top_k=5
        )
        check(
            f"T2 semantic search hits the nested-field text ({len(res_nest)} hits)",
            any("cytochrome" in (r.get("content") or "").lower() for r in res_nest),
        )

        # ----- T3: composite locator + cat --locator round-trip -----
        print("\n--- T3 · composite locator ---")
        # find the doc for (us-east-1, 101)
        sample = next(
            c
            for c in tk_chunks
            if (c.get("locator") or {}).get("ticket_id") == 101
            and (c.get("locator") or {}).get("region") == "us-east-1"
        )
        loc = sample["locator"]
        check(
            f"T3 composite locator carries BOTH fields (got {loc})",
            set(loc) >= {"region", "ticket_id"},
        )
        cat_res = await eng.cat(tk_uri, locator={"region": "eu-west-1", "ticket_id": 100})
        recd = _json.loads(cat_res["content"])
        check(
            "T3 cat --locator composite key returns the right doc",
            recd.get("region") == "eu-west-1"
            and recd.get("ticket_id") == 100
            and "noisy neighbor" in (recd.get("subject") or ""),
        )

        # ----- T4: metadata_fields populated -----
        print("\n--- T4 · metadata_fields populated ---")
        priorities = {(c.get("metadata") or {}).get("priority") for c in tk_chunks}
        assignees = {(c.get("metadata") or {}).get("assignee") for c in tk_chunks}
        check(
            f"T4 priority metadata on every chunk (got {priorities})",
            priorities == {"high", "medium"},
        )
        check(
            f"T4 assignee metadata on every chunk (got {assignees})", assignees == {"alice", "bob"}
        )

        # ----- T5: chunk_max truncation -> 'partial' -----
        print("\n--- T5 · chunk_max truncation ---")
        big_ro = uris.get("/bigcol/documents.jsonl") or {}
        check(
            f"T5 chunk_max=10 caps chunk_count (got {big_ro.get('chunk_count')})",
            big_ro.get("chunk_count") == 10,
        )
        check(
            f"T5 search_status='partial' (got {big_ro.get('search_status')!r})",
            big_ro.get("search_status") == "partial",
        )
        big_hits = await eng.search(
            "topic-2",
            connector_uri=conn_uri,
            object_prefix=conn_uri + "/bigcol",
            mode="hybrid",
            top_k=5,
        )
        check(f"T5 'partial' slice still searchable ({len(big_hits)} hits)", len(big_hits) >= 1)

        # ----- T6: empty collection no-op -----
        print("\n--- T6 · empty collection no-op ---")
        empty_ro = uris.get("/empty/documents.jsonl") or {}
        check(
            f"T6 empty collection: chunks=0, no crash "
            f"(chunks={empty_ro.get('chunk_count')}, "
            f"status={empty_ro.get('search_status')!r})",
            empty_ro.get("chunk_count") == 0 and empty_ro.get("search_status") == "not_indexed",
        )

        # ----- T7: schema_summary chunk -----
        print("\n--- T7 · schema_summary chunk ---")
        sc_uri = conn_uri + "/tickets/schema.json"
        sc_chunks = await asyncio.to_thread(
            eng.milvus.get_chunks_by_object, "default", conn_uri, sc_uri
        )
        kinds = {c.get("chunk_kind") for c in sc_chunks}
        check(
            f"T7 schema.json produces schema_summary (kinds={kinds}, n={len(sc_chunks)})",
            "schema_summary" in kinds,
        )

        # ----- T8: incremental modify reflected -----
        print("\n--- T8 · incremental modify ---")
        await client[DB_NAME].tickets.update_one(
            {"ticket_id": 100, "region": "us-east-1"},
            {
                "$set": {
                    "body": "After identity provider redirect the session token is rotated.",
                    "updatedAt": "2026-05-10T10:00:00Z",
                }
            },
        )
        await eng.add(conn_uri, config=cfg_obj)
        res_mod = await eng.search(
            "session token is rotated", connector_uri=conn_uri, mode="hybrid", top_k=5
        )
        check(
            f"T8 modify reflected in search ({len(res_mod)} hits)",
            any("rotated" in (r.get("content") or "").lower() for r in res_mod),
        )

    finally:
        try:
            eng.milvus.drop_collection("default")
        except Exception:
            pass
        await eng.shutdown()
        await _drop_db(client)
        await client.close()
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'=' * 46}\n  mongo deep e2e: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
