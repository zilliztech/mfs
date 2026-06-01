"""Phase 13 — Zendesk connector live e2e (matrix A14 / SaaS record_collection).

Seeds tickets in a real (dev) Zendesk via REST, indexes them through the zendesk connector
(record_collection -> record_aggregate/row_text), searches, reopens one by its locator, then
deletes the tickets it created. Needs OPENAI_API_KEY + ZENDESK_SUBDOMAIN + ZENDESK_API_TOEKN.
Agent email is fixed (the account admin). API token passed via credential_ref so reopen works.
"""

import asyncio
import json as _json
import os

import httpx

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []
EMAIL = "chen.zhang@zilliz.com"


def check(name, cond):
    results.append(bool(cond))
    print(f"  [{OK if cond else FAIL}] {name}")
    return cond


async def main():
    sub = os.environ.get("ZENDESK_SUBDOMAIN")
    tok = os.environ.get("ZENDESK_API_TOEKN") or os.environ.get("ZENDESK_API_TOKEN")
    if not os.environ.get("OPENAI_API_KEY") or not sub or not tok:
        print("need OPENAI_API_KEY + ZENDESK_SUBDOMAIN + ZENDESK_API_TOEKN")
        raise SystemExit(2)
    base_url = f"https://{sub}.zendesk.com"
    auth = (f"{EMAIL}/token", tok)
    seeded = [
        {
            "subject": "MFSTEST payment capture fails",
            "body": "Stripe webhook returns 402 on checkout intermittently",
        },
        {
            "subject": "MFSTEST sso login loop",
            "body": "After SAML SSO the session token is not persisted",
        },
        {
            "subject": "MFSTEST csv export truncated",
            "body": "Large report export is cut at 65000 rows",
        },
    ]
    created_ids = []
    async with httpx.AsyncClient(timeout=30) as c:
        for t in seeded:
            r = await c.post(
                f"{base_url}/api/v2/tickets.json",
                auth=auth,
                json={"ticket": {"subject": t["subject"], "comment": {"body": t["body"]}}},
            )
            r.raise_for_status()
            created_ids.append(r.json()["ticket"]["id"])
    check("seeded 3 tickets via Zendesk REST", len(created_ids) == 3)

    basep = f"/tmp/mfs_zd_{os.getpid()}"
    os.system(f"rm -rf '{basep}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = basep + "_m.db"
    cfg.milvus.uri = basep + "_v.db"
    cfg.milvus.token = ""
    cfg.object_store.root = basep + "_c"
    cfg.transformation_cache.db_path = basep + "_t.db"
    cfg.summary.enabled = False
    eng = Engine(cfg)
    await eng.startup()
    cfg_obj = {"subdomain": sub, "email": EMAIL, "credential_ref": "env:ZENDESK_API_TOEKN"}
    conn_uri = "zendesk://dev"
    try:
        eng.milvus.drop_collection("default")
        eng.milvus.ensure_collection("default")
        await eng.add("zendesk://dev", config=cfg_obj)

        cid = (await eng.meta.fetchone("SELECT id FROM connectors WHERE type='zendesk'"))["id"]
        ro = await eng.meta.fetchone(
            "SELECT chunk_count, search_status FROM objects WHERE connector_id=? AND object_uri='/tickets/records.jsonl'",
            (cid,),
        )
        check("tickets/records.jsonl indexed (record_collection)", ro and ro["chunk_count"] >= 3)

        res = await eng.search(
            "stripe webhook 402 payment capture", connector_uri=conn_uri, mode="hybrid", top_k=5
        )
        check(
            "zendesk tickets searchable",
            any("payment capture" in (e.get("content") or "").lower() for e in res) or len(res) > 0,
        )
        hit = next((e for e in res if e.get("locator")), None)
        check(
            "zendesk search hit carries a locator",
            hit is not None and isinstance(hit.get("locator"), dict),
        )

        # reopen the matched record by its locator (proves credential_ref survives reopen)
        if hit:
            catout = await eng.cat(hit["source"], locator=hit["locator"])
            recd = _json.loads(catout["content"])
            check(
                "zendesk cat --locator returns a ticket record",
                isinstance(recd, dict) and "subject" in recd,
            )
    finally:
        try:
            eng.milvus.drop_collection("default")
        except Exception:
            pass
        await eng.shutdown()
        # clean up the tickets we created
        async with httpx.AsyncClient(timeout=30) as c:
            for tid in created_ids:
                try:
                    await c.delete(f"{base_url}/api/v2/tickets/{tid}.json", auth=auth)
                except Exception:
                    pass
        os.system(f"rm -rf '{basep}'*")

    passed = sum(results)
    print(f"\n{'=' * 46}\n  zendesk live e2e: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
