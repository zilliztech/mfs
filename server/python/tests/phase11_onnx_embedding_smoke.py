"""Phase 11 — local ONNX embedding provider (fastembed, onnxruntime). Runs with NO
OPENAI_API_KEY to prove it's fully local/closeable: index a small repo and verify
semantic search recall using locally-computed 384-dim embeddings. Milvus Lite.
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
    # explicitly ensure we are NOT relying on OpenAI
    os.environ.pop("OPENAI_API_KEY", None)
    base = f"/tmp/mfs_onnx_{os.getpid()}"
    os.system(f"rm -rf '{base}'*")
    repo = base + "_repo"
    os.makedirs(repo)
    open(repo + "/auth.md", "w").write(
        "# Authentication\nSingle sign-on uses SAML; sessions validate against the token service.\n"
    )
    open(repo + "/billing.md", "w").write(
        "# Billing\nMonthly invoices; refunds issued as partial credits to the customer.\n"
    )
    open(repo + "/deploy.md", "w").write(
        "# Deploy\nThe CI pipeline builds the container image and rolls out to production.\n"
    )

    cfg = load_server_config(apply_env=False)
    cfg.embedding.provider = "onnx"
    cfg.embedding.model = "BAAI/bge-small-en-v1.5"
    cfg.embedding.dim = 384
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
        await eng.add(repo)
        conn = await eng.meta.fetchone("SELECT id FROM connectors WHERE type='file'")
        objs = await eng.meta.fetchall(
            "SELECT search_status FROM objects WHERE connector_id=?", (conn["id"],)
        )
        check(
            "3 docs indexed with onnx embeddings (no OpenAI)",
            len(objs) == 3 and all(o["search_status"] == "indexed" for o in objs),
        )
        check("onnx embeddings produced (api_calls>0, no key needed)", eng.embed.api_calls > 0)

        # semantic recall with local vectors
        r1 = await eng.search("single sign-on identity", mode="semantic", top_k=1)
        check("semantic: SSO query -> auth.md", r1 and r1[0]["source"].endswith("auth.md"))
        r2 = await eng.search("refund a customer charge", mode="semantic", top_k=1)
        check("semantic: refund query -> billing.md", r2 and r2[0]["source"].endswith("billing.md"))
        r3 = await eng.search("ship a release to prod", mode="semantic", top_k=1)
        check("semantic: deploy query -> deploy.md", r3 and r3[0]["source"].endswith("deploy.md"))
    finally:
        try:
            eng.milvus.drop_collection("default")
        except Exception:
            pass
        await eng.shutdown()
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'=' * 46}\n  onnx embedding (local): {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
