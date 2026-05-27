"""Phase 13 — namespace isolation + cross-object cache reuse (matrix B6 / G4 / G3). Lite.

  B6/G4 per_namespace: two namespaces get separate collections; a search in one never
        returns the other's chunks.
  G3 cache reuse: a second connector containing an identical file re-embeds nothing
        (transformation cache is content-addressed). Needs OPENAI_API_KEY.
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
    results.append(bool(cond)); print(f"  [{OK if cond else FAIL}] {name}"); return cond


def _mkcfg(base, ns):
    cfg = load_server_config(apply_env=False)
    cfg.namespace = ns
    cfg.metadata.path = base + "_m.db"; cfg.milvus.uri = base + "_v.db"; cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"; cfg.transformation_cache.db_path = base + "_t.db"
    cfg.milvus.collection_strategy = "per_namespace"
    cfg.summary.enabled = False
    return cfg


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic"); raise SystemExit(2)
    base = f"/tmp/mfs_ns_{os.getpid()}"; os.system(f"rm -rf '{base}'*")
    # shared backend stores, two namespaces
    da = tempfile.mkdtemp(prefix="mfs_ns_a_"); db = tempfile.mkdtemp(prefix="mfs_ns_b_")
    open(f"{da}/a.md", "w").write("# Alpha NS\n\nThe quantum widget calibration routine.\n")
    open(f"{db}/b.md", "w").write("# Beta NS\n\nThe sourdough fermentation schedule.\n")

    eng_a = Engine(_mkcfg(base, "alpha")); await eng_a.startup()
    eng_b = Engine(_mkcfg(base, "beta")); await eng_b.startup()
    try:
        ca = eng_a.milvus.resolve_collection("alpha"); cb = eng_b.milvus.resolve_collection("beta")
        check("per_ns: distinct collection names", ca != cb and "alpha" in ca and "beta" in cb)

        await eng_a.add(da)
        await eng_b.add(db)

        ra = await eng_a.search("sourdough fermentation", mode="hybrid", top_k=5)
        check("G4: ns 'alpha' cannot see ns 'beta' content", not any("b.md" in (e["source"] or "") for e in ra))
        rb = await eng_b.search("quantum widget calibration", mode="hybrid", top_k=5)
        check("G4: ns 'beta' cannot see ns 'alpha' content", not any("a.md" in (e["source"] or "") for e in rb))
        ra2 = await eng_a.search("quantum widget calibration", mode="hybrid", top_k=5)
        check("alpha finds its own content", any("a.md" in (e["source"] or "") for e in ra2))

        # G3 — cross-object embedding cache reuse within one namespace
        d1 = tempfile.mkdtemp(prefix="mfs_g3_1_"); d2 = tempfile.mkdtemp(prefix="mfs_g3_2_")
        same = "# Shared\n\nIdentical content used to prove the content-addressed cache.\n"
        open(f"{d1}/dup.md", "w").write(same); open(f"{d2}/dup.md", "w").write(same)
        await eng_a.add(d1)
        calls = eng_a.embed.api_calls
        await eng_a.add(d2)                       # identical content -> cache hit
        check("G3: identical-content second connector re-embeds nothing", eng_a.embed.api_calls == calls)
        shutil.rmtree(d1, ignore_errors=True); shutil.rmtree(d2, ignore_errors=True)
    finally:
        for e, n in ((eng_a, "alpha"), (eng_b, "beta")):
            try: e.milvus.drop_collection(n)
            except Exception: pass
            await e.shutdown()
        shutil.rmtree(da, ignore_errors=True); shutil.rmtree(db, ignore_errors=True)
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'='*46}\n  namespace isolation + cache reuse: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
