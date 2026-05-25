"""Phase 3 chunk/embed/Milvus + cache smoke — needs OPENAI_API_KEY (run via bash -ic).

  bash -ic 'cd server/python && .venv/bin/python tests/phase3_index_search_smoke.py'

Per Milvus backend (Lite + Zilliz): add a real repo -> assert objects.chunk_count>0
& Milvus count matches -> semantic search_dense returns the relevant object ->
re-add --force-index re-chunks same text but embedding API calls stay flat (tx cache
hit). Monitors embed.api_calls / cache_hits, objects rows, Milvus count.
"""
import asyncio
import os
import shutil
import tempfile

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append((name, cond))
    print(f"  [{OK if cond else FAIL}] {name}")
    return cond


def seed_repo() -> str:
    root = tempfile.mkdtemp(prefix="mfs_p3_repo_")
    os.makedirs(f"{root}/src")
    open(f"{root}/auth.md", "w").write(
        "# Session storage\n\nUser sessions are stored in Redis with a 30 minute TTL. "
        "When a session expires the user must re-authenticate via the SSO provider.\n")
    open(f"{root}/src/store.py", "w").write(
        "class SessionStore:\n"
        "    def save(self, session):\n"
        "        self.redis.setex(session.id, 1800, session.serialize())\n\n"
        "    def load(self, sid):\n"
        "        return self.redis.get(sid)\n")
    open(f"{root}/README.md", "w").write("# Demo repo\n\nNothing about caching here.\n")
    return root


async def run(label: str, milvus_uri: str, milvus_token: str, repo: str):
    print(f"== Phase 3 [{label}] ==")
    base = f"/tmp/mfs_p3_{label}_{os.getpid()}"
    os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_meta.db"
    cfg.milvus.uri = milvus_uri
    cfg.milvus.token = milvus_token
    cfg.object_store.root = base + "_cache"
    cfg.transformation_cache.db_path = base + "_tx.db"

    eng = Engine(cfg)
    await eng.startup()
    try:
        eng.milvus.drop_collection(cfg.namespace)
        eng.milvus.ensure_collection(cfg.namespace)

        await eng.add(repo)
        conn = await eng.meta.fetchone("SELECT id FROM connectors WHERE type='file'")
        objs = await eng.meta.fetchall(
            "SELECT object_uri, chunk_count, search_status FROM objects WHERE connector_id=?", (conn["id"],))
        omap = {o["object_uri"]: o for o in objs}
        check(f"[{label}] auth.md indexed with chunks",
              omap.get("/auth.md", {}).get("chunk_count", 0) > 0 and omap["/auth.md"]["search_status"] == "indexed")
        check(f"[{label}] src/store.py indexed with chunks",
              omap.get("/src/store.py", {}).get("chunk_count", 0) > 0)
        total_chunks = sum(o["chunk_count"] or 0 for o in objs)
        mcount = await asyncio.to_thread(eng.milvus.count, cfg.namespace)
        check(f"[{label}] Milvus count == sum(chunk_count) ({mcount}=={total_chunks})", mcount == total_chunks)

        # semantic search
        qvec = (await eng.embed.batch_embed(["how are user login sessions persisted"]))[0]
        hits = await asyncio.to_thread(eng.milvus.search_dense, cfg.namespace, qvec, 3)
        top_uris = [h["entity"]["object_uri"] if "entity" in h else h.get("object_uri") for h in hits]
        check(f"[{label}] search returns hits", len(hits) > 0)
        check(f"[{label}] top hit is session-related (auth.md/store.py), not README",
              any("auth.md" in (u or "") or "store.py" in (u or "") for u in top_uris[:2]))

        # force-index re-run: re-chunk same text, embeddings must all hit tx cache
        calls_before = eng.embed.api_calls
        hits_before = eng.embed.cache_hits
        await eng.add(repo, full=True)
        check(f"[{label}] force-index: 0 new embedding API calls (cache hit)",
              eng.embed.api_calls == calls_before)
        check(f"[{label}] force-index: cache_hits increased", eng.embed.cache_hits > hits_before)
    finally:
        try:
            eng.milvus.drop_collection(cfg.namespace)
        except Exception:
            pass
        await eng.shutdown()
        os.system(f"rm -rf '{base}'*")


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via: bash -ic '... .venv/bin/python tests/phase3_index_search_smoke.py'")
        raise SystemExit(2)
    repo = seed_repo()
    try:
        await run("Lite", f"/tmp/mfs_p3_lite_{os.getpid()}.db", "", repo)
        if os.environ.get("ZILLIZ_URI") and os.environ.get("ZILLIZ_API_KEY"):
            await run("Zilliz", os.environ["ZILLIZ_URI"], os.environ["ZILLIZ_API_KEY"], repo)
        else:
            print("== Phase 3 [Zilliz] skipped (no creds) ==")
    finally:
        shutil.rmtree(repo, ignore_errors=True)
        os.system(f"rm -rf /tmp/mfs_p3_lite_{os.getpid()}.db*")

    passed = sum(1 for _, c in results if c)
    total = len(results)
    print(f"\n{'='*40}\nPhase 3 index+search: {passed}/{total} checks passed")
    if passed != total:
        print("FAILED:", [n for n, c in results if not c])
        raise SystemExit(1)
    print("ALL PASS")


if __name__ == "__main__":
    asyncio.run(main())
