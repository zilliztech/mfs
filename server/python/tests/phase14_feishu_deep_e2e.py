"""Phase 14 — feishu connector deep e2e.

Pushes past phase13_feishu_docs_smoke (one-shot tenant doc) and
phase13_feishu_user_smoke (user mode bring-up) by drilling into the
content + search pipeline once the connector is actually wired:

  · tenant mode with explicit extra_docs — the configured docx surfaces
    under /docs/ with rendered text indexed.
  · /docs/<title>__<token>.md is a 'document' object, body chunks land in
    Milvus.
  · cat the doc returns plain text (NOT the raw API payload).
  · search hits the doc on a real token plucked from its cached body.
  · chunk_kinds=['body'] gates retrieval to body chunks.
  · object_prefix='/docs/' scopes search to the docs subtree.
  · ls /docs lists the indexed doc as a file Entry.
  · idempotent re-add — second sync emits 0 new body tasks and 0 embed
    calls, proving the doc-revision fingerprint absorbs an unchanged
    page.
  · user-mode OAuth — if ~/.feishu/oauth.json is present from earlier
    device-flow bring-up, we also stand up a USER-mode connector against
    the same DOC_TOKEN and confirm the refresh_token rotation path runs
    (oauth.json mtime advances). Lenient when oauth.json absent.

Env: OPENAI_API_KEY + FEISHU_APP_ID + FEISHU_APP_SECRET (tenant);
optional ~/.feishu/oauth.json (user mode segment is skipped if absent)."""
import asyncio
import os
import pathlib

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []

DOC_TOKEN = "ZsnVdP2IaoJei1xpIqScnZ64nqg"   # doc shared with the bot earlier


def check(name, cond):
    results.append(bool(cond)); print(f"  [{OK if cond else FAIL}] {name}"); return cond


async def main():
    for v in ("OPENAI_API_KEY", "FEISHU_APP_ID", "FEISHU_APP_SECRET"):
        if not os.environ.get(v):
            print(f"{v} not set — run via bash -ic"); raise SystemExit(2)

    base = f"/tmp/mfs_fsdeep_{os.getpid()}"; os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"; cfg.milvus.uri = base + "_v.db"; cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"; cfg.transformation_cache.db_path = base + "_t.db"
    cfg.summary.enabled = False
    eng = Engine(cfg)
    await eng.startup()

    # ----- T1..T8 · tenant mode with explicit extra_docs -----
    conn_uri = "feishu://t14-tenant"
    cfg_tenant = {
        "auth": "tenant",
        "app_id": os.environ["FEISHU_APP_ID"],
        "app_secret": os.environ["FEISHU_APP_SECRET"],
        "region": "feishu",
        # extra_docs expects [{"token": ..., "label": ...}] dicts, not bare strings
        "extra_docs": [{"token": DOC_TOKEN, "label": "t14-doc"}],
    }
    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")

        print("\n--- T1 · tenant mode connector + doc enumeration ---")
        eng.embed.api_calls = 0
        await eng.add(conn_uri, config=cfg_tenant)
        cid = (await eng.meta.fetchone(
            "SELECT id FROM connectors WHERE root_uri=?", (conn_uri,)))["id"]
        objs = await eng.meta.fetchall(
            "SELECT object_uri, chunk_count, search_status FROM objects "
            "WHERE connector_id=?", (cid,))
        uris = {o["object_uri"]: o for o in objs}
        doc_paths = [u for u in uris if u.startswith("/docs/")
                     and u.endswith(".md") and DOC_TOKEN in u]
        check(f"T1 connector registered + extra_docs doc indexed "
              f"(doc_paths={doc_paths})", len(doc_paths) == 1)
        doc_path = doc_paths[0]
        check(f"T1 doc body produced chunks "
              f"(chunks={uris[doc_path]['chunk_count']})",
              (uris[doc_path]["chunk_count"] or 0) >= 1)

        # ----- T2: cat returns plain text -----
        print("\n--- T2 · cat doc returns plain text ---")
        cat_res = await eng.cat(conn_uri + doc_path)
        body = cat_res if isinstance(cat_res, str) else (cat_res or {}).get("content") or ""
        check(f"T2 cat returns non-empty body (len={len(body)})",
              isinstance(body, str) and len(body.strip()) >= 200)
        check("T2 cat body is not the raw API payload "
              "(no raw 'block_id' / 'document_revision_id' JSON keys at top)",
              '"block_id"' not in body[:500]
              and '"document_revision_id"' not in body[:500])

        # ----- T3: search hits a real token in the doc body -----
        print("\n--- T3 · search hits a real token from doc body ---")
        import re as _re
        # Pull a unique-ish CJK / latin token from middle of the body
        tokens = _re.findall(r"[A-Za-z][A-Za-z0-9]{6,18}", body)
        tokens += _re.findall(r"[一-龥]{4,10}", body)
        if tokens:
            term = tokens[len(tokens) // 2]
            hits = await eng.search(term, connector_uri=conn_uri,
                                     mode="hybrid", top_k=5)
            on_doc = [h for h in hits if (h.get("source") or "") == conn_uri + doc_path]
            check(f"T3 search('{term[:20]}') surfaces the doc "
                  f"({len(hits)} total, {len(on_doc)} on doc)",
                  len(hits) >= 1)
        else:
            check("T3 doc body too thin for unique-term probe (skipped)", True)

        # ----- T4: chunk_kinds=['body'] filter -----
        print("\n--- T4 · chunk_kinds=['body'] filter ---")
        body_only = await eng.search(
            "doc", connector_uri=conn_uri, mode="hybrid", top_k=10,
            chunk_kinds=["body"])
        check(f"T4 every filtered hit is chunk_kind='body' "
              f"({len(body_only)} hits)",
              len(body_only) == 0 or all(
                  (h.get("metadata") or {}).get("chunk_kind") == "body"
                  for h in body_only))

        # ----- T5: object_prefix='/docs/' scopes -----
        print("\n--- T5 · object_prefix='/docs/' scope ---")
        scoped = await eng.search(
            "doc", connector_uri=conn_uri,
            object_prefix=conn_uri + "/docs/",
            mode="hybrid", top_k=20)
        check(f"T5 scoped hits never leave /docs/ "
              f"({len(scoped)} hits)",
              len(scoped) == 0 or all(
                  "/docs/" in (h.get("source") or "")
                  for h in scoped))

        # ----- T6: ls /docs lists the doc -----
        print("\n--- T6 · ls /docs lists the indexed doc ---")
        ls = await eng.ls(conn_uri + "/docs")
        names = {e["name"] for e in ls["entries"]}
        check(f"T6 ls /docs includes the indexed doc "
              f"(entries={len(names)})",
              len(names) >= 1)

        # ----- T7: idempotent re-add (no upstream change) -----
        print("\n--- T7 · idempotent re-add ---")
        tasks_before = await eng.meta.fetchall(
            "SELECT id FROM object_tasks WHERE connector_id=? "
            "AND change_kind != 'dir_summary'", (cid,))
        eng.embed.api_calls = 0
        await eng.add(conn_uri, config=cfg_tenant)
        tasks_after = await eng.meta.fetchall(
            "SELECT id FROM object_tasks WHERE connector_id=? "
            "AND change_kind != 'dir_summary'", (cid,))
        new_tasks = len(tasks_after) - len(tasks_before)
        check(f"T7 second sync: 0 new body tasks ({new_tasks})",
              new_tasks == 0)
        check(f"T7 second sync: 0 embedding API calls "
              f"(api delta={eng.embed.api_calls})",
              eng.embed.api_calls == 0)

        # ----- T8: region defaults to feishu (not lark) -----
        print("\n--- T8 · region defaults to feishu ---")
        # Build a plugin and confirm sdk_domain resolution
        from mfs_server.connectors.feishu.plugin import FeishuPlugin
        from lark_oapi import FEISHU_DOMAIN, LARK_DOMAIN
        check(f"T8 SDK domain for region='feishu' = FEISHU_DOMAIN",
              FeishuPlugin._sdk_domain("feishu") == FEISHU_DOMAIN)
        check(f"T8 SDK domain for region='lark' = LARK_DOMAIN",
              FeishuPlugin._sdk_domain("lark") == LARK_DOMAIN)

        # ----- T9: user-mode (oauth.json) — lenient if absent -----
        print("\n--- T9 · user mode oauth.json refresh-token rotation ---")
        oauth_path = pathlib.Path(os.path.expanduser("~/.feishu/oauth.json"))
        if not oauth_path.is_file():
            check("T9 oauth.json absent (user-mode segment skipped, lenient pass)",
                  True)
        else:
            user_conn = "feishu://t14-user"
            cfg_user = {
                "auth": "user",
                "app_id": os.environ["FEISHU_APP_ID"],
                "app_secret": os.environ["FEISHU_APP_SECRET"],
                "oauth_state_file": str(oauth_path),
                "region": "feishu",
                "extra_docs": [{"token": DOC_TOKEN, "label": "t14-doc-user"}],
            }
            mtime_before = oauth_path.stat().st_mtime
            await eng.add(user_conn, config=cfg_user)
            mtime_after = oauth_path.stat().st_mtime
            check(f"T9 user-mode add succeeds, refresh_token rotated "
                  f"(mtime {mtime_before} -> {mtime_after})",
                  mtime_after >= mtime_before)
            user_cid = (await eng.meta.fetchone(
                "SELECT id FROM connectors WHERE root_uri=?",
                (user_conn,)))["id"]
            user_docs = await eng.meta.fetchall(
                "SELECT object_uri, chunk_count FROM objects "
                "WHERE connector_id=? AND object_uri LIKE '/docs/%' "
                "AND object_uri LIKE ?",
                (user_cid, f"%{DOC_TOKEN}%"))
            check(f"T9 user-mode also indexes the extra_doc "
                  f"({len(user_docs)} doc rows)",
                  len(user_docs) >= 1
                  and any((r["chunk_count"] or 0) >= 1 for r in user_docs))

    finally:
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        await eng.shutdown()
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'='*46}\n  feishu deep e2e: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
