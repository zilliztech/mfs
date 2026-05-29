"""Phase 14 — hubspot connector deep e2e.

Drives the live HubSpot CRM API (hubspot-api-client wrapped in
asyncio.to_thread) against the user's free CRM portal. The portal has
HubSpot's onboarding fixture: 2 contacts + 1 company, no deals. We use
the empty 'deals' object as our 'empty collection no-op' case.

  · multi-object enumeration — contacts / companies / deals each surface
    as /<object>/records.jsonl + the deals path lands as not_indexed
    (chunk_count=0) without crashing.
  · contacts (2 records) and companies (1 record) indexed; chunks carry
    {id} locator + properties flattened into the record.
  · ls structure — root lists the 3 configured objects; each subdir has
    records.jsonl.
  · cat --locator on a contact reopens the exact record by id.
  · search hits a real token from the cached records (HubSpot tutorial
    contact uses bh@hubspot.com).
  · chunk_kinds=['row_text'] gates retrieval.
  · object_prefix='/contacts/' scopes search.
  · chunk_max user override -> partial state on contacts (2 records ->
    cap=1 -> partial, slice still searchable).
  · cat --range slices the contacts records.jsonl by row.
  · search keyword mode requires the literal term; semantic mode doesn't.
  · idempotent re-add — plugin returns fingerprint=None so every object
    re-yields as modified on every sync (like slack); tx_cache absorbs
    the embedding cost so api_calls stays at 0.

Tests two findings explicitly so a future plugin fix flips the assertion:
  · plugin._DEFAULT_OBJECTS includes 'tickets' which only exists in
    Service Hub portals; Free CRM portals 403 on tickets sync.
  · plugin.fingerprint always returns None so object change-detection
    is degraded to 'always modified'.

Env: HUBSPOT_ACCESS_TOKEN + OPENAI_API_KEY (bash -ic)."""
import asyncio
import json as _json
import os

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append(bool(cond)); print(f"  [{OK if cond else FAIL}] {name}"); return cond


async def main():
    for v in ("OPENAI_API_KEY", "HUBSPOT_ACCESS_TOKEN"):
        if not os.environ.get(v):
            print(f"{v} not set — run via bash -ic"); raise SystemExit(2)

    base = f"/tmp/mfs_hsdeep_{os.getpid()}"; os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"; cfg.milvus.uri = base + "_v.db"; cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"; cfg.transformation_cache.db_path = base + "_t.db"
    cfg.summary.enabled = False
    eng = Engine(cfg)
    await eng.startup()

    conn_uri = "hubspot://t14"
    # Skip the plugin's default 'tickets' (Service Hub object; Free CRM
    # portals 403 on it). object_types is the plugin's enumerate list;
    # objects is the framework's [[objects]] match config — using two
    # distinct keys avoids the collision the plugin would otherwise hit.
    cfg_obj = {
        "credential_ref": "env:HUBSPOT_ACCESS_TOKEN",
        "object_types": ["contacts", "companies", "deals"],
        "max_read_rows": 50,
        "objects": [
            {"match": "/contacts/records.jsonl",
             "text_fields": ["email", "firstname", "lastname", "company", "phone"],
             "locator_fields": ["id"],
             "metadata_fields": ["createdate", "lastmodifieddate"]},
            {"match": "/companies/records.jsonl",
             "text_fields": ["name", "domain", "industry", "city", "description"],
             "locator_fields": ["id"],
             "metadata_fields": ["createdate", "hs_lastmodifieddate"]},
            {"match": "/deals/records.jsonl",
             "text_fields": ["dealname", "description"],
             "locator_fields": ["id"]},
        ],
    }

    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")

        # ----- T1: multi-object enumeration ----------
        print("\n--- T1 · multi-object enumeration ---")
        await eng.add(conn_uri, config=cfg_obj)
        cid = (await eng.meta.fetchone(
            "SELECT id FROM connectors WHERE root_uri=?", (conn_uri,)))["id"]
        objs = await eng.meta.fetchall(
            "SELECT object_uri, chunk_count, search_status FROM objects "
            "WHERE connector_id=?", (cid,))
        uris = {o["object_uri"]: o for o in objs}
        print(f"  DEBUG objects: {sorted(uris)}")
        want = {"/contacts/records.jsonl", "/companies/records.jsonl",
                 "/deals/records.jsonl"}
        check(f"T1 contacts + companies + deals all enumerate "
              f"(missing={sorted(want - set(uris))})",
              want <= set(uris))

        # ----- T2: contacts indexed (2 records) ----------
        print("\n--- T2 · contacts indexed ---")
        contacts_ro = uris.get("/contacts/records.jsonl") or {}
        check(f"T2 contacts has >=1 chunk "
              f"(got {contacts_ro.get('chunk_count')})",
              (contacts_ro.get("chunk_count") or 0) >= 1)
        check(f"T2 contacts search_status='indexed' "
              f"(got {contacts_ro.get('search_status')!r})",
              contacts_ro.get("search_status") == "indexed")

        # ----- T3: companies indexed (1 record) ----------
        print("\n--- T3 · companies indexed ---")
        companies_ro = uris.get("/companies/records.jsonl") or {}
        check(f"T3 companies has >=1 chunk "
              f"(got {companies_ro.get('chunk_count')})",
              (companies_ro.get("chunk_count") or 0) >= 1)

        # ----- T4: deals empty -> not_indexed, no crash ----------
        print("\n--- T4 · empty deals object lands cleanly ---")
        deals_ro = uris.get("/deals/records.jsonl") or {}
        check(f"T4 empty deals: chunks=0, status='not_indexed' "
              f"(chunks={deals_ro.get('chunk_count')}, "
              f"status={deals_ro.get('search_status')!r})",
              deals_ro.get("chunk_count") == 0
              and deals_ro.get("search_status") == "not_indexed")

        # ----- T5: chunks carry {id} locator + flattened properties --------
        print("\n--- T5 · chunks have {id} locator ---")
        contacts_uri = conn_uri + "/contacts/records.jsonl"
        contacts_chunks = await asyncio.to_thread(
            eng.milvus.get_chunks_by_object, "default", conn_uri, contacts_uri)
        ids = [(c.get("locator") or {}).get("id") for c in contacts_chunks]
        check(f"T5 every contact chunk has a {{'id': ...}} locator "
              f"(sample={ids[:2]})",
              all(isinstance(i, str) and i.isdigit() for i in ids if i))

        # ----- T6: search hits a real token from contact records --------
        print("\n--- T6 · search hits a real contact field ---")
        joined = " | ".join((c.get("content") or "") for c in contacts_chunks)
        check(f"T6 cached contact chunks contain known HubSpot tutorial "
              f"fixtures (Brian / hubspot.com)",
              "Brian" in joined or "hubspot.com" in joined)
        hits = await eng.search("hubspot.com email",
                                 connector_uri=conn_uri, mode="hybrid", top_k=5)
        on_contacts = [h for h in hits
                       if "/contacts/" in (h.get("source") or "")]
        check(f"T6 search lands on contacts subtree "
              f"({len(hits)} total, {len(on_contacts)} on contacts)",
              len(on_contacts) >= 1)

        # ----- T7: cat --locator round-trips ---------
        print("\n--- T7 · cat --locator on a contact ---")
        sample_id = next((i for i in ids if i), None)
        if sample_id:
            cat_res = await eng.cat(contacts_uri, locator={"id": sample_id})
            rec = _json.loads(cat_res["content"])
            check(f"T7 cat --locator {{'id': '{sample_id}'}} returns the record",
                  isinstance(rec, dict) and rec.get("id") == sample_id)
        else:
            check("T7 no sample id available (skipped)", False)

        # ----- T8: ls structure ---------
        print("\n--- T8 · ls structure ---")
        ls_root = await eng.ls(conn_uri)
        root_names = {e["name"] for e in ls_root["entries"]}
        check(f"T8 ls / lists the 3 configured objects "
              f"(got {sorted(root_names)})",
              {"contacts", "companies", "deals"} <= root_names)
        ls_contacts = await eng.ls(conn_uri + "/contacts")
        contacts_subdir = {e["name"] for e in ls_contacts["entries"]}
        check(f"T8 ls /contacts contains records.jsonl "
              f"(got {sorted(contacts_subdir)})",
              "records.jsonl" in contacts_subdir)

        # ----- T9: chunk_max user override -> partial ------
        print("\n--- T9 · chunk_max=1 truncates contacts -> partial ---")
        cfg_capped = {
            "credential_ref": "env:HUBSPOT_ACCESS_TOKEN",
            "object_types": ["contacts"],
            "objects": [{
                "match": "/contacts/records.jsonl",
                "text_fields": ["email", "firstname", "lastname", "company"],
                "locator_fields": ["id"],
                "chunk_max": 1,
            }],
        }
        await eng.add("hubspot://t14-capped", config=cfg_capped)
        cap_cid = (await eng.meta.fetchone(
            "SELECT id FROM connectors WHERE root_uri='hubspot://t14-capped'"))["id"]
        cap_ro = await eng.meta.fetchone(
            "SELECT chunk_count, search_status FROM objects "
            "WHERE connector_id=? AND object_uri=?",
            (cap_cid, "/contacts/records.jsonl"))
        # Only triggers 'partial' when there are >=2 contacts; portal has 2.
        check(f"T9 chunk_max=1 caps contacts "
              f"(chunks={cap_ro['chunk_count'] if cap_ro else None})",
              cap_ro and cap_ro["chunk_count"] == 1)
        check(f"T9 search_status='partial' on truncated contacts "
              f"(got {cap_ro['search_status'] if cap_ro else None!r})",
              cap_ro and cap_ro["search_status"] == "partial")

        # ----- T10: chunk_kinds + object_prefix gating --------
        print("\n--- T10 · chunk_kinds + object_prefix filters ---")
        row_only = await eng.search(
            "contact", connector_uri=conn_uri, mode="hybrid", top_k=5,
            chunk_kinds=["row_text"])
        check(f"T10 chunk_kinds=['row_text'] gates retrieval "
              f"({len(row_only)} hits)",
              len(row_only) == 0 or all(
                  (h.get("metadata") or {}).get("chunk_kind") == "row_text"
                  for h in row_only))
        scoped = await eng.search(
            "data", connector_uri=conn_uri,
            object_prefix=conn_uri + "/contacts/",
            mode="hybrid", top_k=5)
        check(f"T10 object_prefix='/contacts/' scopes search "
              f"({len(scoped)} hits)",
              len(scoped) == 0 or all(
                  "/contacts/" in (h.get("source") or "")
                  for h in scoped))

        # ----- T11: cat --range on contacts.jsonl ------------
        print("\n--- T11 · cat --range slices records.jsonl ---")
        sliced = await eng.cat(contacts_uri, range=(0, 2))
        lines = [ln for ln in (sliced or "").splitlines() if ln.strip()]
        decoded = [_json.loads(ln) for ln in lines]
        check(f"T11 cat --range (0,2) returns 2 contact records "
              f"(got {len(decoded)})", len(decoded) == 2)
        check("T11 each sliced record carries an id",
              all(isinstance(r.get("id"), str) for r in decoded))

        # ----- T12: keyword vs semantic search modes ---------
        print("\n--- T12 · keyword vs semantic search ---")
        # 'hubspot' literally appears in contact email + company domain
        kw_hits = await eng.search("hubspot", connector_uri=conn_uri,
                                    mode="keyword", top_k=5)
        sem_hits = await eng.search("crm software vendor",
                                     connector_uri=conn_uri,
                                     mode="semantic", top_k=5)
        check(f"T12 keyword('hubspot') returns hit with literal term "
              f"({len(kw_hits)} hits)",
              any("hubspot" in (h.get("content") or "").lower()
                  for h in kw_hits))
        check(f"T12 semantic search returns hits "
              f"({len(sem_hits)} hits)", len(sem_hits) >= 1)

        # ----- T13: idempotent re-add — fingerprint=None FINDING -----
        # plugin.fingerprint returns None unconditionally, so every object
        # re-yields as 'modified' on the second sync. Embedding cost still
        # goes to 0 (tx_cache), but task queue is fully re-walked.
        print("\n--- T13 · re-add: fingerprint=None forces re-walk (finding) ---")
        tasks_before = await eng.meta.fetchall(
            "SELECT id FROM object_tasks WHERE connector_id=? "
            "AND change_kind != 'dir_summary'", (cid,))
        eng.embed.api_calls = 0
        await eng.add(conn_uri, config=cfg_obj)
        tasks_after = await eng.meta.fetchall(
            "SELECT id FROM object_tasks WHERE connector_id=? "
            "AND change_kind != 'dir_summary'", (cid,))
        delta_tasks = len(tasks_after) - len(tasks_before)
        check(f"T13 finding: plugin.fingerprint=None forces every object to "
              f"re-yield as modified ({delta_tasks} new tasks; expected >=3)",
              delta_tasks >= 3)
        check(f"T13 second sync embeds 0 chunks — tx_cache absorbs the cost "
              f"(api delta={eng.embed.api_calls})",
              eng.embed.api_calls == 0)

        # ----- T14: connect-time probe drops portal-unavailable objects ----
        # _DEFAULT_OBJECTS still includes 'tickets' (Service Hub object) but
        # the connect-time probe ought to drop it on a Free CRM portal that
        # 403s on tickets.basic_api.get_page. Verify both: defaults still
        # list it AND a NO-CONFIG add lands the right object set
        # (contacts/companies/deals only, no tickets).
        print("\n--- T14 · connect-time probe filters portal-unavailable objects ---")
        from mfs_server.connectors.hubspot.plugin import _DEFAULT_OBJECTS
        check(f"T14 _DEFAULT_OBJECTS still ships with tickets "
              f"(Service Hub users get it for free) (got {_DEFAULT_OBJECTS})",
              "tickets" in _DEFAULT_OBJECTS)
        # Add a connector with NEITHER object_types NOR objects — plugin
        # must fall back to probing _DEFAULT_OBJECTS and silently drop the
        # ones this portal rejects.
        await eng.add("hubspot://t14-probe",
                       config={"credential_ref": "env:HUBSPOT_ACCESS_TOKEN"})
        probe_cid = (await eng.meta.fetchone(
            "SELECT id FROM connectors WHERE root_uri='hubspot://t14-probe'"))["id"]
        probe_objs = await eng.meta.fetchall(
            "SELECT object_uri FROM objects WHERE connector_id=?", (probe_cid,))
        probe_paths = {o["object_uri"] for o in probe_objs}
        check(f"T14 probe path: Free CRM enumerates contacts+companies+deals "
              f"(got {sorted(probe_paths)})",
              {"/contacts/records.jsonl", "/companies/records.jsonl",
               "/deals/records.jsonl"} <= probe_paths)
        check(f"T14 probe path: tickets dropped — Service Hub not enabled "
              f"(got {sorted(probe_paths)})",
              "/tickets/records.jsonl" not in probe_paths)

    finally:
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        await eng.shutdown()
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'='*46}\n  hubspot deep e2e: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
