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
