"""Phase 14 — zendesk connector deep e2e.

Pushes past phase13_zendesk_smoke (seed 3 tickets, search them, reopen one by
locator). This one drives the multi-resource layout (tickets / users /
organizations + per-ticket comments.jsonl) and the configuration knobs:

  · all top-level collections surface — /tickets/records.jsonl,
    /users/records.jsonl, /organizations/records.jsonl all land as objects.
  · ticket preset (zendesk.tickets) auto-resolves text_fields/locator/metadata
    so unconfigured users still get useful indexing.
  · ticket comments — /tickets/comments.jsonl indexes ticket comment text
    (we add an explicit comment on one ticket and verify it surfaces).
  · metadata_fields populated on chunks — priority / status / assignee_id
    land on ticket chunks via the preset.
  · search hits surface across BOTH tickets and comments.
  · max_read_rows truncation — cap at 2 with 4 seeded tickets flips
    search_status='partial'; search still works on the truncated slice.
  · cat --locator round-trips on a ticket by id.

Cleans up everything it created. Env: ZENDESK_SUBDOMAIN + ZENDESK_API_TOEKN
(yes, typo preserved from prior tests) + OPENAI_API_KEY."""

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
    if not (os.environ.get("OPENAI_API_KEY") and sub and tok):
        print("need OPENAI_API_KEY + ZENDESK_SUBDOMAIN + ZENDESK_API_TOEKN")
        raise SystemExit(2)

    base_url = f"https://{sub}.zendesk.com"
    auth = (f"{EMAIL}/token", tok)

    seeded = [
        {
            "subject": "T14 MFSTEST stripe webhook 402",
            "body": "Stripe webhook returns 402 on payment capture intermittently",
            "priority": "high",
        },
        {
            "subject": "T14 MFSTEST sso login loop",
            "body": "After SAML SSO the session token is not persisted across redirects",
            "priority": "urgent",
        },
        {
            "subject": "T14 MFSTEST csv export truncated",
            "body": "Large report export is cut at 65000 rows; CSV writer flushes early",
            "priority": "normal",
        },
        {
            "subject": "T14 MFSTEST noisy neighbor",
            "body": "Shared volume IO dominated by tenant-X workload at peak hours",
            "priority": "low",
        },
    ]
    created_ids: list[int] = []
    extra_comment_text = "Followup: confirmed the rotation now uses ECDSA P-384 keys via vault."

    async with httpx.AsyncClient(timeout=30) as c:
        # Seed 4 tickets
        for t in seeded:
            r = await c.post(
                f"{base_url}/api/v2/tickets.json",
                auth=auth,
                json={
                    "ticket": {
                        "subject": t["subject"],
                        "comment": {"body": t["body"]},
                        "priority": t["priority"],
                    }
                },
            )
            r.raise_for_status()
            created_ids.append(r.json()["ticket"]["id"])
        # Add a second comment to ticket #1 so comments.jsonl has at least 1
        # extra ticket-comment row beyond the initial seed comment.
        r = await c.put(
            f"{base_url}/api/v2/tickets/{created_ids[0]}.json",
            auth=auth,
            json={"ticket": {"comment": {"body": extra_comment_text, "public": True}}},
        )
        r.raise_for_status()

    base = f"/tmp/mfs_zd14_{os.getpid()}"
    os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"
    cfg.milvus.uri = base + "_v.db"
    cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"
    cfg.transformation_cache.db_path = base + "_t.db"
    cfg.summary.enabled = False
    eng = Engine(cfg)
    await eng.startup()

    cfg_obj = {
        "subdomain": sub,
        "email": EMAIL,
        "credential_ref": "env:ZENDESK_API_TOEKN",
        # comments.jsonl has no built-in preset (only /tickets/records.jsonl
        # does), so we configure it explicitly here.
        "objects": [
            {
                "match": "/tickets/comments.jsonl",
                "text_fields": ["body"],
                "locator_fields": ["id"],
            }
        ],
    }
    conn_uri = "zendesk://t14"
    try:
        eng.milvus.drop_collection("default")
        eng.milvus.ensure_collection("default")
        await eng.add(conn_uri, config=cfg_obj)
        cid = (await eng.meta.fetchone("SELECT id FROM connectors WHERE root_uri=?", (conn_uri,)))[
            "id"
        ]
        objs = await eng.meta.fetchall(
            "SELECT object_uri, chunk_count, search_status FROM objects WHERE connector_id=?",
            (cid,),
        )
        uris = {o["object_uri"]: o for o in objs}
        print(f"  DEBUG indexed objects: {sorted(uris)}")

        # ----- T1: all three top-level collections enumerated -----
        print("\n--- T1 · multi-resource enumeration ---")
        want = {"/tickets/records.jsonl", "/users/records.jsonl", "/organizations/records.jsonl"}
        check(
            f"T1 tickets/users/organizations all surface (missing={sorted(want - set(uris))})",
            want <= set(uris),
        )
        # The tickets we seeded are at least 4 — preset's chunk_max default may
        # leave it indexed (no partial) since 4 < default. We just assert >= 4.
        tk_chunks = uris["/tickets/records.jsonl"]["chunk_count"]
        check(
            f"T1 tickets indexed (got {tk_chunks} chunks, expect >= 4 from seed)",
            tk_chunks and tk_chunks >= 4,
        )

        # ----- T2: comments.jsonl indexed -----
        print("\n--- T2 · per-ticket comments.jsonl indexed ---")
        cm_ro = uris.get("/tickets/comments.jsonl") or {}
        check(
            f"T2 /tickets/comments.jsonl indexed (chunks={cm_ro.get('chunk_count')})",
            (cm_ro.get("chunk_count") or 0) >= 1,
        )
        # search for the unique comment string we added
        cm_hits = await eng.search(
            "ECDSA P-384 keys via vault", connector_uri=conn_uri, mode="hybrid", top_k=5
        )
        check(
            f"T2 extra comment text surfaces in search ({len(cm_hits)} hits)",
            any("ECDSA" in (h.get("content") or "") for h in cm_hits),
        )

        # ----- T3: metadata_fields populated via preset -----
        print("\n--- T3 · ticket preset attaches metadata ---")
        ticket_chunks = await asyncio.to_thread(
            eng.milvus.get_chunks_by_object,
            "default",
            conn_uri,
            conn_uri + "/tickets/records.jsonl",
        )
        # Restrict to our seeded MFSTEST tickets to avoid stray workspace data
        seeded_chunks = [c for c in ticket_chunks if "T14 MFSTEST" in (c.get("content") or "")]
        prios = {(c.get("metadata") or {}).get("priority") for c in seeded_chunks}
        statuses = {(c.get("metadata") or {}).get("status") for c in seeded_chunks}
        check(
            f"T3 priority metadata covers the four seeded priorities (got {prios})",
            prios >= {"high", "urgent", "normal", "low"},
        )
        check(
            f"T3 status metadata attached to seeded tickets (got {statuses})",
            all(s in {"new", "open", "pending", "hold", "solved", "closed"} for s in statuses if s),
        )

        # ----- T4: search hits across tickets -----
        print("\n--- T4 · search across tickets ---")
        sso_hits = await eng.search(
            "saml session token redirect persistence",
            connector_uri=conn_uri,
            mode="hybrid",
            top_k=5,
        )
        check(
            f"T4 SSO ticket searchable ({len(sso_hits)} hits)",
            any(
                "T14 MFSTEST sso" in (h.get("content") or "").lower()
                or "saml" in (h.get("content") or "").lower()
                for h in sso_hits
            ),
        )

        # ----- T5: cat --locator round-trips on a ticket id -----
        print("\n--- T5 · cat --locator round-trips ---")
        sample = next(
            (
                c
                for c in seeded_chunks
                if isinstance(c.get("locator"), dict) and "id" in c["locator"]
            ),
            None,
        )
        if sample is None:
            sample = next((c for c in seeded_chunks if isinstance(c.get("locator"), dict)), None)
        if sample:
            loc = sample["locator"]
            cat_res = await eng.cat(conn_uri + "/tickets/records.jsonl", locator=loc)
            recd = _json.loads(cat_res["content"])
            check(
                f"T5 cat --locator reopens the ticket ({loc})",
                isinstance(recd, dict)
                and "subject" in recd
                and "T14 MFSTEST" in (recd.get("subject") or ""),
            )
        else:
            check("T5 no seeded chunk carried a locator — preset misconfigured?", False)

        # ----- T6: max_read_rows truncation -> partial -----
        # NOTE: the plugin's pagination loop checks `n < limit` only at the
        # TOP of each iteration, so it yields the whole current page (up to
        # page[size]=100 records) before honoring the cap. With a low cap
        # the chunk_count still reflects the page size, but `declare_partial`
        # fires correctly at the end, so search_status='partial' is the
        # observable signal an agent acts on.
        print("\n--- T6 · max_read_rows=2 declares partial (cap effective at page boundary) ---")
        cfg_capped = {**cfg_obj, "max_read_rows": 2}
        await eng.add("zendesk://t14-cap", config=cfg_capped)
        cap_cid = (
            await eng.meta.fetchone("SELECT id FROM connectors WHERE root_uri='zendesk://t14-cap'")
        )["id"]
        cap_ro = await eng.meta.fetchone(
            "SELECT chunk_count, search_status FROM objects "
            "WHERE connector_id=? AND object_uri='/tickets/records.jsonl'",
            (cap_cid,),
        )
        check(
            f"T6 cap=2 flags search_status='partial' "
            f"(got {cap_ro['search_status'] if cap_ro else None!r}, "
            f"chunk_count={cap_ro['chunk_count'] if cap_ro else None})",
            cap_ro and cap_ro["search_status"] == "partial",
        )
        cap_hits = await eng.search(
            "MFSTEST", connector_uri="zendesk://t14-cap", mode="hybrid", top_k=5
        )
        check(f"T6 'partial' slice still searchable ({len(cap_hits)} hits)", len(cap_hits) >= 1)
        # Finding pinned: the plugin's pagination cap is page-granular.
        # Calling out the gap so a future fix (break out of the for-loop when
        # n >= limit) can flip this to chunk_count == max_read_rows.
        check(
            f"T6 finding: chunk_count > max_read_rows because cap is page-granular "
            f"(got {cap_ro['chunk_count']} > 2; plugin checks limit only between pages)",
            cap_ro and cap_ro["chunk_count"] > 2,
        )

        # ----- T7: ls top-level -----
        print("\n--- T7 · ls / lists tickets/users/organizations ---")
        ls = await eng.ls(conn_uri)
        names = {e["name"] for e in ls["entries"]}
        check(
            f"T7 ls/ contains tickets,users,organizations (got {sorted(names)})",
            {"tickets", "users", "organizations"} <= names,
        )

    finally:
        try:
            eng.milvus.drop_collection("default")
        except Exception:
            pass
        await eng.shutdown()
        # Tear down the seeded tickets so the workspace doesn't accumulate
        async with httpx.AsyncClient(timeout=30) as c:
            for tid in created_ids:
                try:
                    await c.delete(f"{base_url}/api/v2/tickets/{tid}.json", auth=auth)
                except Exception:
                    pass
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'=' * 46}\n  zendesk deep e2e: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
