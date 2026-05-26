"""Phase 12 — connector management: probe / inspect / remove. Needs OPENAI_API_KEY
(bash -ic). Milvus Lite. Verifies remove actually purges objects + Milvus chunks.
"""
import asyncio
import os

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append(bool(cond)); print(f"  [{OK if cond else FAIL}] {name}"); return cond


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic"); raise SystemExit(2)
    base = f"/tmp/mfs_cm_{os.getpid()}"; os.system(f"rm -rf '{base}'*")
    repo = base + "_repo"; os.makedirs(repo)
    open(repo + "/a.md", "w").write("# A\nSingle sign-on via SAML token service.\n")
    open(repo + "/b.md", "w").write("# B\nbilling refunds as partial credits.\n")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"; cfg.milvus.uri = base + "_v.db"; cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"; cfg.transformation_cache.db_path = base + "_t.db"
    eng = Engine(cfg)
    await eng.startup()
    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")

        # probe a path before adding (file connector always healthy)
        pr = await eng.probe(repo)
        check("probe ok=True for a local dir", pr["ok"] is True and pr["type"] == "file")

        await eng.add(repo)
        curi, _ = eng.resolve_connector_uri(repo)

        ins = await eng.inspect(repo)
        check("inspect: 2 objects, chunks>0", ins and ins["object_count"] == 2 and ins["chunk_count"] >= 2)
        check("inspect: job succeeded", ins["jobs"].get("succeeded") == 1)

        before = await asyncio.to_thread(eng.milvus.count, eng.ns, f'connector_uri == "{curi}"')
        check("Milvus has chunks before remove", before >= 2)
        # search works pre-remove
        r = await eng.search("single sign-on", connector_uri=curi, top_k=2)
        check("search hits before remove", bool(r))

        # remove
        ok = await eng.remove_connector(repo)
        check("remove_connector returns True", ok is True)
        gone = await eng.meta.fetchone("SELECT id FROM connectors WHERE root_uri=?", (curi,))
        check("connector row gone", gone is None)
        objs = await eng.meta.fetchall(
            "SELECT count(*) AS n FROM objects WHERE object_uri LIKE '%a.md'", ())
        check("objects purged", objs[0]["n"] == 0)
        after = await asyncio.to_thread(eng.milvus.count, eng.ns, f'connector_uri == "{curi}"')
        check("Milvus chunks purged after remove", after == 0)
        # inspect now 404-equivalent (None)
        check("inspect after remove -> None", await eng.inspect(repo) is None)
    finally:
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        await eng.shutdown(); os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'='*46}\n  connector mgmt: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
