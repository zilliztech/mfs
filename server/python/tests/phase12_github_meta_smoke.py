"""Phase 12 — GitHub issues/PRs (_meta/issues.jsonl, pulls.jsonl, pulls/<n>/diff.patch).
Indexes a small slice of a public repo's collaboration data (capped via max_read_rows).
Needs GITHUB_TOKEN + OPENAI_API_KEY (bash -ic). Milvus Lite.
"""

import asyncio
import os

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append(bool(cond))
    print(f"  [{OK if cond else FAIL}] {name}")
    return cond


async def main():
    if not (os.environ.get("OPENAI_API_KEY") and os.environ.get("GITHUB_TOKEN")):
        print("need OPENAI_API_KEY + GITHUB_TOKEN — run via bash -ic")
        raise SystemExit(2)
    base = f"/tmp/mfs_ghm_{os.getpid()}"
    os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"
    cfg.milvus.uri = base + "_v.db"
    cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"
    cfg.transformation_cache.db_path = base + "_t.db"

    eng = Engine(cfg)
    await eng.startup()
    try:
        eng.milvus.drop_collection("default")
        eng.milvus.ensure_collection("default")
        # cli/CLI: github connector with capped _meta + text_fields for issues/pulls
        gh_config = {
            "repo": "octocat/Hello-World",
            "index_meta": True,
            "max_read_rows": 3,
            "objects": [
                {
                    "match": "*issues.jsonl",
                    "text_fields": ["title", "body"],
                    "locator_fields": ["number"],
                    "chunk_strategy": "per_row",
                    "chunk_max": 3,
                },
                {
                    "match": "*pulls.jsonl",
                    "text_fields": ["title", "body"],
                    "locator_fields": ["number"],
                    "chunk_strategy": "per_row",
                    "chunk_max": 3,
                },
            ],
        }
        await eng.add("github://hello", config=gh_config)
        conn = await eng.meta.fetchone("SELECT id FROM connectors WHERE type='github'")
        cid = conn["id"]

        iss = await eng.meta.fetchone(
            "SELECT chunk_count, search_status FROM objects WHERE connector_id=? AND object_uri='/_meta/issues.jsonl'",
            (cid,),
        )
        # 'partial' is correct when max_read_rows caps the read (octocat/Hello-World has >3 issues)
        check(
            "issues.jsonl indexed (record_collection)",
            iss and iss["chunk_count"] >= 1 and iss["search_status"] in ("indexed", "partial"),
        )
        pl = await eng.meta.fetchone(
            "SELECT chunk_count FROM objects WHERE connector_id=? AND object_uri='/_meta/pulls.jsonl'",
            (cid,),
        )
        check("pulls.jsonl indexed", pl and pl["chunk_count"] >= 1)
        diffs = await eng.meta.fetchall(
            "SELECT object_uri, chunk_count FROM objects WHERE connector_id=? AND object_uri LIKE '/_meta/pulls/%/diff.patch'",
            (cid,),
        )
        check(
            "at least one PR diff.patch document indexed",
            len(diffs) >= 1 and any(d["chunk_count"] >= 1 for d in diffs),
        )

        # ls the _meta subtree via the generalized browse path
        entries = (await eng.ls("github://hello/_meta"))["entries"]
        names = {e["name"] for e in entries}
        check("ls _meta shows issues/pulls", {"issues.jsonl", "pulls.jsonl"} <= names)

        # an issue is reopenable by locator
        res = await eng.search(
            "hello world",
            connector_uri="github://hello",
            object_prefix="github://hello/_meta/issues.jsonl",
            mode="hybrid",
            top_k=3,
        )
        check(
            "issues searchable + carry number locator",
            bool(res) and (res[0].get("locator") or {}).get("number") is not None,
        )
    finally:
        try:
            eng.milvus.drop_collection("default")
        except Exception:
            pass
        await eng.shutdown()
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'=' * 46}\n  github issues/pulls: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
