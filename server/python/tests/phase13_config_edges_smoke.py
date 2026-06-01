"""Phase 13 — round-2 config / credential / cache edges (R7.1/7.2/7.4, R8.1).

R7.1/R7.2 are pure credential-resolution checks (no services). R7.4/R8.1 drive the file
connector. Needs OPENAI_API_KEY for R7.4/R8.1. Lite.
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
    results.append(bool(cond))
    print(f"  [{OK if cond else FAIL}] {name}")
    return cond


def _mkcfg(base):
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"
    cfg.milvus.uri = base + "_v.db"
    cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"
    cfg.transformation_cache.db_path = base + "_t.db"
    cfg.summary.enabled = False
    return cfg


def _credential_edges():
    # R7.1 missing env var -> clear error (not silent empty)
    os.environ.pop("MFS_TEST_MISSING_XYZ", None)
    try:
        Engine._resolve_ref("env:MFS_TEST_MISSING_XYZ")
        check("R7.1 missing env credential_ref raises", False)
    except ValueError as e:
        check("R7.1 missing env credential_ref raises clear error", "not set" in str(e))
    # present env resolves
    os.environ["MFS_TEST_PRESENT_XYZ"] = "topsecret"
    check(
        "R7.1 present env resolves", Engine._resolve_ref("env:MFS_TEST_PRESENT_XYZ") == "topsecret"
    )
    # R7.2 unimplemented schemes -> error, never used as literal
    for scheme in ("secret:app/key", "vault:kv/data/app"):
        try:
            Engine._resolve_ref(scheme)
            check(f"R7.2 {scheme.split(':')[0]}: rejected", False)
        except ValueError as e:
            check(
                f"R7.2 {scheme.split(':')[0]}: rejected (not implemented)",
                "not implemented" in str(e),
            )
    # missing file -> error
    try:
        Engine._resolve_ref("file:/no/such/secret/file")
        check("R7.1 missing secret file raises", False)
    except ValueError as e:
        check("R7.1 missing secret file raises", "cannot read" in str(e))
    # plain value passes through unchanged
    check("plain value passes through", Engine._resolve_ref("just-a-value") == "just-a-value")


async def main():
    _credential_edges()
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — skipping R7.4/R8.1 (run via bash -ic for full)")
    else:
        # R7.4 indexable=false -> recorded, not chunked, not searchable
        root = tempfile.mkdtemp(prefix="mfs_cfg_")
        open(f"{root}/keep.md", "w").write("# Keep\n\nthis document is indexed normally.\n")
        open(f"{root}/skip.secret", "w").write("# Secret\n\nthis must be metadata-only.\n")
        base = f"/tmp/mfs_cfg_{os.getpid()}"
        os.system(f"rm -rf '{base}'*")
        eng = Engine(_mkcfg(base))
        await eng.startup()
        conn_uri = f"file://local{root}"
        try:
            eng.milvus.drop_collection("default")
            eng.milvus.ensure_collection("default")
            await eng.add(root, config={"objects": [{"match": "*.secret", "indexable": False}]})
            sk = await eng.meta.fetchone(
                "SELECT chunk_count FROM objects o JOIN connectors c ON o.connector_id=c.id "
                "WHERE c.root_uri=? AND o.object_uri='/skip.secret'",
                (conn_uri,),
            )
            check(
                "R7.4 indexable=false recorded but 0 chunks",
                sk is not None and sk["chunk_count"] == 0,
            )
            res = await eng.search(
                "metadata-only secret", connector_uri=conn_uri, mode="hybrid", top_k=5
            )
            check(
                "R7.4 indexable=false not searchable",
                not any("skip.secret" in (e["source"] or "") for e in res),
            )
            check(
                "R7.4 sibling still indexed",
                any(
                    "keep.md" in (e["source"] or "")
                    for e in await eng.search(
                        "document indexed normally", connector_uri=conn_uri, mode="hybrid", top_k=5
                    )
                ),
            )
        finally:
            try:
                eng.milvus.drop_collection("default")
            except Exception:
                pass
            await eng.shutdown()
            shutil.rmtree(root, ignore_errors=True)
            os.system(f"rm -rf '{base}'*")

        # R8.1 transformation cache disabled -> passthrough, still works, re-index re-embeds
        root2 = tempfile.mkdtemp(prefix="mfs_nocache_")
        open(f"{root2}/d.md", "w").write("# D\n\nvector search without a transformation cache.\n")
        base2 = f"/tmp/mfs_nocache_{os.getpid()}"
        os.system(f"rm -rf '{base2}'*")
        cfg2 = _mkcfg(base2)
        cfg2.transformation_cache.enabled = False
        eng2 = Engine(cfg2)
        await eng2.startup()
        conn2 = f"file://local{root2}"
        try:
            eng2.milvus.drop_collection("default")
            eng2.milvus.ensure_collection("default")
            await eng2.add(root2)
            res = await eng2.search(
                "vector search transformation cache", connector_uri=conn2, mode="hybrid", top_k=3
            )
            check("R8.1 cache-disabled: index+search works", len(res) > 0)
            calls = eng2.embed.api_calls
            await eng2.add(root2, full=True)
            check(
                "R8.1 cache-disabled: force re-index re-embeds (no cache reuse)",
                eng2.embed.api_calls > calls,
            )
        finally:
            try:
                eng2.milvus.drop_collection("default")
            except Exception:
                pass
            await eng2.shutdown()
            shutil.rmtree(root2, ignore_errors=True)
            os.system(f"rm -rf '{base2}'*")

    passed = sum(results)
    print(f"\n{'=' * 46}\n  config/credential/cache edges: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
