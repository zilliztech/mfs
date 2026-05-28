"""Phase 13 — feishu connector user-OAuth mode live e2e.

Drives the engine through the real Feishu Open API with a user_access_token
obtained via OAuth 2.0 Device Flow (one-shot, run beforehand via
`python -m mfs_server.connectors.feishu.auth_login`).

Verifies the THREE things user-mode is supposed to deliver:
  1. connect() succeeds — refresh_token exchange returns a fresh access_token
  2. refresh_token ROTATION + write-back — Feishu's one-shot refresh_token
     gets replaced in oauth.json each connect (the bug we hit in the dial-tone)
  3. Acting as the human — read the user's own docx body via extra_docs

The user's chat.list returns 0 (p2p only, no groups) and drive root returns
0 (their personal drive is empty), so we don't assert on those. The extra_docs
path is what proves the user_access_token is actually being passed through.

Env: OPENAI_API_KEY + a populated ~/.feishu/oauth.json. Lite-ish."""
import asyncio
import json as _json
import os
import pathlib

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []

DOC_TOKEN = "ZsnVdP2IaoJei1xpIqScnZ64nqg"          # the user's test docx (shared with bot earlier)
OAUTH_FILE = pathlib.Path.home() / ".feishu" / "oauth.json"


def check(name, cond):
    results.append(bool(cond)); print(f"  [{OK if cond else FAIL}] {name}"); return cond


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic"); raise SystemExit(2)
    if not OAUTH_FILE.exists():
        print(f"{OAUTH_FILE} missing — run "
              "`python -m mfs_server.connectors.feishu.auth_login --output ~/.feishu/oauth.json`")
        raise SystemExit(2)

    # snapshot refresh_token + mtime BEFORE — connect() must rotate it
    pre_blob = _json.loads(OAUTH_FILE.read_text())
    pre_rt = pre_blob["refresh_token"]
    pre_mtime = OAUTH_FILE.stat().st_mtime

    base = f"/tmp/mfs_fs_user_{os.getpid()}"; os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"; cfg.milvus.uri = base + "_v.db"; cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"; cfg.transformation_cache.db_path = base + "_t.db"
    eng = Engine(cfg)
    await eng.startup()

    conn_uri = "feishu://user-e2e"
    cfg_obj = {
        "auth": "user",
        "oauth_state_file": str(OAUTH_FILE),
        "extra_docs": [{"token": DOC_TOKEN, "label": "1M-context-claude-code"}],
    }
    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")
        await eng.add(conn_uri, config=cfg_obj)

        # 1) connector registered (= connect() succeeded with user token)
        crow = await eng.meta.fetchone("SELECT id FROM connectors WHERE type='feishu'")
        check("feishu connector registered (user mode)", crow is not None)
        cid = crow["id"]

        # 2) refresh_token ROTATED + written back — this is the critical bug-fix check
        post_blob = _json.loads(OAUTH_FILE.read_text())
        post_rt = post_blob["refresh_token"]
        post_mtime = OAUTH_FILE.stat().st_mtime
        check("oauth.json was written by connect() (mtime advanced)", post_mtime > pre_mtime)
        check("refresh_token rotated (new value != old value)", post_rt != pre_rt)
        check("oauth.json kept app_id + app_secret", post_blob.get("app_id") and post_blob.get("app_secret"))

        # 3) extra_docs got indexed via user_access_token
        doc_objs = await eng.meta.fetchall(
            "SELECT object_uri, chunk_count, search_status FROM objects "
            "WHERE connector_id=? AND object_uri LIKE '/docs/%'", (cid,))
        check(f"docs subtree contains the extra_doc (got {len(doc_objs)} doc paths)",
              len(doc_objs) == 1)
        if doc_objs:
            d = doc_objs[0]
            check(f"doc indexed with >=1 chunk via USER token (got {d['chunk_count']})",
                  (d["chunk_count"] or 0) >= 1)

            full_uri = conn_uri + d["object_uri"]
            content = await eng.cat(full_uri)
            check(f"cat returns markdown body via USER token ({len(content) if isinstance(content,str) else '?'} chars)",
                  isinstance(content, str) and len(content) > 100)

            res = await eng.search("1M context Claude Code DeepSeek",
                                   connector_uri=conn_uri, mode="hybrid", top_k=5)
            on_us = [r for r in res if (r.get("source") or "").startswith(conn_uri)]
            check(f"search returns >=1 hit on this connector (got {len(on_us)})",
                  len(on_us) >= 1)
    finally:
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        await eng.shutdown()
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'='*46}\n  feishu user-mode live: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
