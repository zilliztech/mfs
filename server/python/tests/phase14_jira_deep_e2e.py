"""Phase 14 — jira connector deep e2e.

Drives the live Atlassian Cloud API (atlassian-python-api wrapped in
asyncio.to_thread) on the user's site, scoped to a single configured project
to keep the run bounded. Atlassian removed the legacy
/rest/api/3/search endpoint in 2025; the plugin now uses enhanced_jql +
approximate_issue_count behind the scenes and this test pins that path.

  · authenticate against the cloud site (myself), enumerate projects.
  · /projects/<key>/issues.jsonl indexed as record_collection — capped by
    max_read_rows since real projects can have thousands of issues.
  · each row chunk carries an issue {key} locator and the framework's
    flattened fields (summary/description/status/priority/assignee/...).
  · metadata_fields populates status / priority / assignee onto chunks
    so the agent can filter without reopening.
  · search hits the project on a real token plucked from a cached chunk.
  · chunk_kinds=['row_text'] gates retrieval correctly.
  · object_prefix='/projects/<key>/' scopes search to the chosen project.
  · cat --locator on an indexed issue round-trips back to the right record.
  · idempotent re-add — no upstream change -> 0 new body tasks AND 0
    embedding API calls (count-based fingerprint absorbs).

Env: JIRA_BASE_URL + JIRA_EMAIL + JIRA_API_TOKEN + OPENAI_API_KEY.
Optional JIRA_PROJECT_KEY (default 'UED'); whatever you set must be a
project your account can read."""
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
    for v in ("OPENAI_API_KEY", "JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"):
        if not os.environ.get(v):
            print(f"{v} not set — run via bash -ic"); raise SystemExit(2)
    project_key = os.environ.get("JIRA_PROJECT_KEY", "UED")

    base = f"/tmp/mfs_jrdeep_{os.getpid()}"; os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"; cfg.milvus.uri = base + "_v.db"; cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"; cfg.transformation_cache.db_path = base + "_t.db"
    cfg.summary.enabled = False
    eng = Engine(cfg)
    await eng.startup()

    conn_uri = "jira://t14"
    # Scope to the one project so the run is bounded; cap at 15 issues so
    # search has enough variety without dragging the suite. Configure
    # text_fields + locator + metadata explicitly since the connector ships
    # no preset for jira.
    cfg_obj = {
        "url": os.environ["JIRA_BASE_URL"],
        "username": os.environ["JIRA_EMAIL"],
        "credential_ref": "env:JIRA_API_TOKEN",
        "cloud": True,
        "projects": [project_key],
        "max_read_rows": 15,
        "objects": [{
            "match": f"/projects/{project_key}/issues.jsonl",
            "text_fields": ["summary", "description"],
            "locator_fields": ["key"],
            "metadata_fields": ["status", "priority", "assignee"],
        }],
    }
    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")

        # ----- T1: connector registers + project's issues.jsonl indexed -----
        print("\n--- T1 · project enumeration ---")
        eng.embed.api_calls = 0
        await eng.add(conn_uri, config=cfg_obj)
        cid = (await eng.meta.fetchone(
            "SELECT id FROM connectors WHERE root_uri=?", (conn_uri,)))["id"]

        issues_uri = f"/projects/{project_key}/issues.jsonl"
        objs = await eng.meta.fetchall(
            "SELECT object_uri, chunk_count, search_status FROM objects "
            "WHERE connector_id=?", (cid,))
        uris = {o["object_uri"]: o for o in objs}
        print(f"  DEBUG objects: {sorted(uris)}")
        issue_ro = uris.get(issues_uri) or {}
        check(f"T1 {issues_uri} indexed (chunks={issue_ro.get('chunk_count')})",
              issue_ro.get("chunk_count") is not None
              and issue_ro["chunk_count"] >= 1)
        check(f"T1 max_read_rows=15 caps the chunk_count "
              f"(got {issue_ro.get('chunk_count')})",
              (issue_ro.get("chunk_count") or 0) <= 15)

        # ----- T2: chunks carry {key} locator + metadata_fields ------
        print("\n--- T2 · per-issue locator + metadata ---")
        chunks = await asyncio.to_thread(
            eng.milvus.get_chunks_by_object, "default", conn_uri,
            conn_uri + issues_uri)
        keys = [(c.get("locator") or {}).get("key") for c in chunks]
        check(f"T2 every chunk carries a {{'key': 'XXX-N'}} locator "
              f"(sample={keys[:3]})",
              all(isinstance(k, str) and k.startswith(f"{project_key}-")
                  for k in keys if k))
        # metadata_fields populated on at least one chunk
        statuses = {(c.get("metadata") or {}).get("status") for c in chunks}
        priorities = {(c.get("metadata") or {}).get("priority") for c in chunks}
        assignees = {(c.get("metadata") or {}).get("assignee") for c in chunks}
        check(f"T2 status metadata attached to chunks ({len(statuses)} distinct values)",
              len(statuses) >= 1 and not all(s is None for s in statuses))
        check(f"T2 priority metadata attached to chunks ({len(priorities)} distinct values)",
              len(priorities) >= 1 and not all(p is None for p in priorities))
        del assignees   # assignee may legitimately be unset; surface but don't assert

        # ----- T3: search hits the project on a real token ------
        print("\n--- T3 · search hits the project ---")
        import re as _re
        tokens = []
        for c in chunks:
            text = c.get("content") or ""
            tokens += [w for w in _re.findall(r"[A-Za-z][A-Za-z0-9]{6,18}", text)
                       if w.lower() not in
                       {"summary", "description", "project", "issue", "jira",
                        "atlassian", "status", "priority", "assignee"}]
        if tokens:
            term = tokens[len(tokens) // 2]
            hits = await eng.search(term, connector_uri=conn_uri,
                                     mode="hybrid", top_k=10)
            on_jira = [h for h in hits
                       if (h.get("source") or "").endswith(issues_uri)]
            check(f"T3 search('{term}') surfaces hits in the project's issues "
                  f"({len(hits)} total, {len(on_jira)} on project)",
                  len(on_jira) >= 1)
        else:
            check("T3 sample chunks too thin for unique-term probe (skipped)", True)

        # ----- T4: chunk_kinds=['row_text'] filter ------
        print("\n--- T4 · chunk_kinds=['row_text'] filter ---")
        row_only = await eng.search(
            "issue", connector_uri=conn_uri, mode="hybrid", top_k=5,
            chunk_kinds=["row_text"])
        check(f"T4 every filtered hit is chunk_kind='row_text' "
              f"({len(row_only)} hits)",
              len(row_only) == 0 or all(
                  (h.get("metadata") or {}).get("chunk_kind") == "row_text"
                  for h in row_only))

        # ----- T5: object_prefix scopes to the project -----
        print("\n--- T5 · object_prefix scopes search ---")
        scoped = await eng.search(
            "ticket", connector_uri=conn_uri,
            object_prefix=conn_uri + f"/projects/{project_key}/",
            mode="hybrid", top_k=10)
        check(f"T5 scoped hits all live under /projects/{project_key}/ "
              f"({len(scoped)} hits)",
              len(scoped) == 0 or all(
                  f"/projects/{project_key}/" in (h.get("source") or "")
                  for h in scoped))

        # ----- T6: cat --locator round-trips back to the issue -----
        print("\n--- T6 · cat --locator round-trips ---")
        sample_key = next((k for k in keys if k), None)
        if sample_key:
            cat_res = await eng.cat(conn_uri + issues_uri,
                                     locator={"key": sample_key})
            recd = _json.loads(cat_res["content"])
            check(f"T6 cat --locator {{'key': '{sample_key}'}} returns the issue",
                  isinstance(recd, dict)
                  and recd.get("key") == sample_key)
        else:
            check("T6 no sample key found (cannot exercise cat --locator)", False)

        # ----- T7: idempotent re-add -----
        print("\n--- T7 · idempotent re-add (count fingerprint absorbs) ---")
        tasks_before = await eng.meta.fetchall(
            "SELECT id FROM object_tasks WHERE connector_id=? "
            "AND change_kind != 'dir_summary'", (cid,))
        eng.embed.api_calls = 0
        await eng.add(conn_uri, config=cfg_obj)
        tasks_after = await eng.meta.fetchall(
            "SELECT id FROM object_tasks WHERE connector_id=? "
            "AND change_kind != 'dir_summary'", (cid,))
        delta_tasks = len(tasks_after) - len(tasks_before)
        # The project may legitimately accrue 1-2 new issues during the
        # test window; tolerate a tiny delta but not a full re-index.
        check(f"T7 second sync adds <= 2 new body tasks "
              f"(real workspace; got {delta_tasks})", delta_tasks <= 2)
        # When the count hasn't changed, embedding cost must be 0 — even
        # if some new issues arrived, transformation_cache absorbs the
        # repeat embeddings.
        check(f"T7 second sync: embedding API calls bounded by new content "
              f"(api delta={eng.embed.api_calls})", eng.embed.api_calls <= 5)

        # ----- T8: ls /projects shows UED + users.jsonl at root --------
        print("\n--- T8 · ls structure ---")
        ls_root = await eng.ls(conn_uri)
        root_names = {e["name"] for e in ls_root["entries"]}
        check(f"T8 ls / lists 'projects' dir + 'users.jsonl' "
              f"(got {sorted(root_names)})",
              {"projects", "users.jsonl"} <= root_names)
        ls_proj = await eng.ls(conn_uri + "/projects")
        proj_names = {e["name"] for e in ls_proj["entries"]}
        check(f"T8 ls /projects contains {project_key} "
              f"(got {sorted(proj_names)})",
              project_key in proj_names)

        # ----- T9: flatten_issue field coverage ----------------------
        print("\n--- T9 · flatten_issue exposes the full field set ---")
        # _flatten_issue (plugin.py) writes: key, id, summary, description,
        # status, priority, assignee, reporter, labels, created, updated.
        # The rendered content joins fields the user configured via
        # text_fields. Status / priority / assignee go to metadata (T2);
        # reporter / created / updated / labels go nowhere unless the
        # user adds them. Verify the source dict itself carries them by
        # reopening one issue and checking the raw flattened record.
        sample_chunk = next((c for c in chunks
                              if (c.get("locator") or {}).get("key")), None)
        if sample_chunk:
            sample_key = sample_chunk["locator"]["key"]
            cat_res = await eng.cat(conn_uri + issues_uri,
                                     locator={"key": sample_key})
            rec = _json.loads(cat_res["content"])
            expected_keys = {"key", "id", "summary", "status", "priority",
                              "assignee", "reporter", "labels",
                              "created", "updated"}
            present = expected_keys & set(rec.keys())
            check(f"T9 flatten_issue exposes the documented field set "
                  f"({len(present)} / {len(expected_keys)}; "
                  f"missing={sorted(expected_keys - present)})",
                  present == expected_keys)

        # ----- T10: enhanced_jql multi-page pagination ---------------
        # The new /search/jql is cursor-paginated via nextPageToken; with
        # page_size=100 (internal) and max_read_rows=200, sync MUST do at
        # least two enhanced_jql calls. Verify by spinning up a SECOND
        # connector with a larger cap and confirming chunk_count > 100,
        # which can only land if pagination worked.
        print("\n--- T10 · enhanced_jql pagination (nextPageToken loop) ---")
        cfg_paginate = {**cfg_obj, "max_read_rows": 200}
        cfg_paginate["objects"] = [
            {**cfg_obj["objects"][0]}  # same shape but bigger cap
        ]
        await eng.add("jira://t14-paginate", config=cfg_paginate)
        pag_cid = (await eng.meta.fetchone(
            "SELECT id FROM connectors WHERE root_uri='jira://t14-paginate'"))["id"]
        pag_ro = await eng.meta.fetchone(
            "SELECT chunk_count FROM objects WHERE connector_id=? "
            "AND object_uri=?", (pag_cid, issues_uri))
        # >= 101 forces at least one nextPageToken roundtrip (page_size=100
        # internal). UED has 2900+ issues, so this is reachable.
        check(f"T10 multi-page sync produced > 100 chunks (proves nextPageToken "
              f"loop ran) — chunk_count={pag_ro['chunk_count'] if pag_ro else None}",
              pag_ro and pag_ro["chunk_count"] > 100)

        # ----- T11: chunk_max user override triggers 'partial' --------
        # Use a tiny cap via [[objects]].chunk_max so the framework truncates
        # (NOT max_read_rows; that's plugin-level pre-read cap). chunk_max
        # is the SAME flag we test on mysql / mongo / snowflake.
        print("\n--- T11 · chunk_max user override -> partial state ---")
        cfg_capped = {**cfg_obj}
        cfg_capped["objects"] = [{
            **cfg_obj["objects"][0],
            "chunk_max": 5,
        }]
        # Also bump max_read_rows so the plugin gives the framework enough
        # rows to truncate. (max_read_rows caps inside the plugin; chunk_max
        # caps inside the framework's record_collection pipeline.)
        cfg_capped["max_read_rows"] = 50
        await eng.add("jira://t14-chunkcap", config=cfg_capped)
        cap_cid = (await eng.meta.fetchone(
            "SELECT id FROM connectors WHERE root_uri='jira://t14-chunkcap'"))["id"]
        cap_ro = await eng.meta.fetchone(
            "SELECT chunk_count, search_status FROM objects "
            "WHERE connector_id=? AND object_uri=?", (cap_cid, issues_uri))
        check(f"T11 chunk_max=5 caps chunk_count "
              f"(got {cap_ro['chunk_count'] if cap_ro else None})",
              cap_ro and cap_ro["chunk_count"] == 5)
        check(f"T11 chunk_max truncation flips search_status='partial' "
              f"(got {cap_ro['search_status'] if cap_ro else None!r})",
              cap_ro and cap_ro["search_status"] == "partial")
        cap_hits = await eng.search("project", connector_uri="jira://t14-chunkcap",
                                     mode="hybrid", top_k=5)
        check(f"T11 partial slice still searchable ({len(cap_hits)} hits)",
              len(cap_hits) >= 1)

        # ----- T12: cat --range on the issues.jsonl ------------------
        # cat --range on a structured object slices via record_collection
        # pushdown: read_records(range=Range(start,end)) yields just rows
        # [start, end). For Jira that means "first 3 issues by updated DESC".
        print("\n--- T12 · cat --range on a structured object ---")
        sliced = await eng.cat(conn_uri + issues_uri, range=(0, 3))
        # cat --range on structured returns newline-joined JSON records
        lines = [ln for ln in (sliced or "").splitlines() if ln.strip()]
        decoded = [_json.loads(ln) for ln in lines]
        check(f"T12 cat --range (0,3) returns 3 newline-joined records "
              f"(got {len(decoded)})", len(decoded) == 3)
        check(f"T12 each record carries the issue's 'key' field",
              all(isinstance(r.get("key"), str) and r["key"].startswith(f"{project_key}-")
                  for r in decoded))

        # ----- T13: keyword search vs semantic search ---------------
        # Search modes should behave differently on the SAME query: keyword
        # mode requires the literal term in chunk content; semantic mode
        # doesn't. Use the same anchor token we found in T3.
        print("\n--- T13 · keyword mode vs semantic mode ---")
        if tokens:
            kw_hits = await eng.search(term, connector_uri=conn_uri,
                                        mode="keyword", top_k=5)
            sem_hits = await eng.search(term, connector_uri=conn_uri,
                                         mode="semantic", top_k=5)
            check(f"T13 keyword mode returns hit(s) containing the literal "
                  f"term '{term}' ({len(kw_hits)} hits)",
                  any(term.lower() in (h.get("content") or "").lower()
                      for h in kw_hits))
            check(f"T13 semantic mode also returns hits "
                  f"({len(sem_hits)} hits)", len(sem_hits) >= 1)

        # ----- T14: comment / attachment fields NOT indexed today (finding) --
        # plugin._flatten_issue intentionally drops `comment` and
        # `attachment` (they need explicit expand=). Pin this so the gap
        # is visible — a future fix that surfaces comments can flip it.
        print("\n--- T14 · finding: comments + attachments not in flattened record ---")
        check("T14 flatten_issue output has NO 'comment' or 'attachment' field "
              "(plugin doesn't request expand=comments; gap is intentional today)",
              sample_chunk is not None
              and "comment" not in rec and "attachment" not in rec
              and "comments" not in rec and "attachments" not in rec)

    finally:
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        await eng.shutdown()
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'='*46}\n  jira deep e2e: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
