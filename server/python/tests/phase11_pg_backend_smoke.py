"""Phase 11 — Postgres metadata + transformation_cache backend (asyncpg).
Runs a real add -> index -> search with BOTH metadata and tx_cache on Postgres
(local mfstest db), Milvus Lite for vectors. Asserts rows land in PG and the cache
hits on re-run. Needs local PG (db mfstest) + OPENAI_API_KEY (bash -ic). Lite.
"""
import asyncio
import os

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []
PG_DSN = "postgresql://zhangchen@/mfstest?host=/var/run/postgresql"
_MFS_TABLES = ["file_state", "object_tasks", "connector_jobs", "objects", "connector_state",
               "artifact_cache", "watch_grants", "connectors", "schema_version",
               "transformation_cache"]


def check(name, cond):
    results.append(bool(cond))
    print(f"  [{OK if cond else FAIL}] {name}")
    return cond


async def _reset_pg():
    import asyncpg
    conn = await asyncpg.connect(PG_DSN)
    for t in _MFS_TABLES:
        await conn.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    await conn.close()


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic"); raise SystemExit(2)
    await _reset_pg()

    base = f"/tmp/mfs_p11_{os.getpid()}"
    os.system(f"rm -rf '{base}'*")
    repo = base + "_repo"; os.makedirs(repo, exist_ok=True)
    open(repo + "/auth.md", "w").write(
        "# Auth\nSingle sign-on via SAML; sessions validate against the token service.\n")
    open(repo + "/cache.md", "w").write(
        "# Caching\nResults are memoized in a content-addressable transformation cache.\n")

    cfg = load_server_config(apply_env=False)
    cfg.metadata.backend = "postgres"; cfg.metadata.dsn = PG_DSN
    cfg.transformation_cache.backend = "postgres"; cfg.transformation_cache.dsn = PG_DSN
    cfg.milvus.uri = base + "_milvus.db"; cfg.milvus.token = ""
    cfg.object_store.root = base + "_cache"

    eng = Engine(cfg)
    await eng.startup()
    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")
        await eng.add(repo)

        # metadata rows live in PG
        conn = await eng.meta.fetchone("SELECT id FROM connectors WHERE type='file'")
        check("PG: connector row present", conn is not None)
        objs = await eng.meta.fetchall(
            "SELECT object_uri, chunk_count, search_status FROM objects WHERE connector_id=?", (conn["id"],))
        check("PG: 2 objects indexed", len(objs) == 2 and all(o["search_status"] == "indexed" for o in objs))
        jobs = await eng.meta.fetchall("SELECT status FROM connector_jobs")
        check("PG: job succeeded", jobs and jobs[0]["status"] == "succeeded")

        # search works through PG-backed metadata
        res = await eng.search("single sign-on token", mode="hybrid", top_k=2)
        check("search top is auth.md", res and res[0]["source"].endswith("auth.md"))

        # tx cache populated in PG; re-run hits cache (no new embedding calls)
        st = await eng.tx_cache.stats()
        check("PG tx_cache has entries", st.get("entry_count", 0) > 0)
        calls_before = eng.embed.api_calls
        await eng.add(repo, full=True)        # force re-index → must hit tx cache
        check("re-index hits PG tx cache (0 new embed calls)", eng.embed.api_calls == calls_before)
    finally:
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        await eng.shutdown()
        await _reset_pg()
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'='*46}\n  PG backend: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
