"""Phase 4 search modes smoke — needs OPENAI_API_KEY (run via bash -ic).

Per backend (Lite + Zilliz): add a repo, then exercise engine.search in hybrid /
semantic / keyword modes + collapse. Asserts session-related objects rank above the
unrelated README, and keyword(BM25 'redis') hits the files literally containing it.
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
    root = tempfile.mkdtemp(prefix="mfs_p4_repo_")
    os.makedirs(f"{root}/src")
    open(f"{root}/auth.md", "w").write(
        "# Session storage\n\nUser sessions are stored in Redis with a 30 minute TTL. "
        "When a session expires the user must re-authenticate.\n"
    )
    open(f"{root}/src/store.py", "w").write(
        "class SessionStore:\n    def save(self, session):\n"
        "        self.redis.setex(session.id, 1800, session.serialize())\n"
    )
    open(f"{root}/README.md", "w").write("# Demo\n\nA project about unrelated banana recipes.\n")
    return root


async def run(label: str, uri: str, token: str, repo: str):
    print(f"== Phase 4 [{label}] ==")
    base = f"/tmp/mfs_p4_{label}_{os.getpid()}"
    os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_meta.db"
    cfg.milvus.uri = uri
    cfg.milvus.token = token
    cfg.object_store.root = base + "_cache"
    cfg.transformation_cache.db_path = base + "_tx.db"
    eng = Engine(cfg)
    await eng.startup()
    try:
        eng.milvus.drop_collection(cfg.namespace)
        eng.milvus.ensure_collection(cfg.namespace)
        conn_uri = f"file://local{repo}"
        await eng.add(repo)

        def session_related(envs, n=2):
            top = [(e["source"] or "") for e in envs[:n]]
            return any("auth.md" in s or "store.py" in s for s in top) and all(
                "README" not in s for s in top
            )

        hy = await eng.search(
            "how are user login sessions stored", connector_uri=conn_uri, mode="hybrid", top_k=5
        )
        check(f"[{label}] hybrid returns results", len(hy) > 0)
        check(f"[{label}] hybrid: session objects rank above README", session_related(hy))

        se = await eng.search(
            "how are user login sessions stored", connector_uri=conn_uri, mode="semantic", top_k=5
        )
        check(f"[{label}] semantic: session objects top", session_related(se))

        kw = await eng.search("redis", connector_uri=conn_uri, mode="keyword", top_k=5)
        kw_sources = {e["source"] for e in kw}
        check(
            f"[{label}] keyword 'redis' hits redis-containing files",
            any("auth.md" in (s or "") or "store.py" in (s or "") for s in kw_sources),
        )
        check(
            f"[{label}] keyword excludes unrelated README",
            not any("README" in (s or "") for s in kw_sources),
        )

        col = await eng.search(
            "session", connector_uri=conn_uri, mode="hybrid", top_k=10, collapse=True
        )
        srcs = [e["source"] for e in col]
        check(f"[{label}] collapse: sources unique", len(srcs) == len(set(srcs)))

        # envelope shape
        if hy:
            e = hy[0]
            check(
                f"[{label}] envelope has source/content/score/metadata",
                all(k in e for k in ("source", "content", "score", "metadata"))
                and "chunk_kind" in e["metadata"],
            )
    finally:
        try:
            eng.milvus.drop_collection(cfg.namespace)
        except Exception:
            pass
        await eng.shutdown()
        os.system(f"rm -rf '{base}'*")


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic")
        raise SystemExit(2)
    repo = seed_repo()
    try:
        await run("Lite", f"/tmp/mfs_p4_lite_{os.getpid()}.db", "", repo)
        if os.environ.get("ZILLIZ_URI") and os.environ.get("ZILLIZ_API_KEY"):
            await run("Zilliz", os.environ["ZILLIZ_URI"], os.environ["ZILLIZ_API_KEY"], repo)
        else:
            print("== Phase 4 [Zilliz] skipped ==")
    finally:
        shutil.rmtree(repo, ignore_errors=True)
        os.system(f"rm -rf /tmp/mfs_p4_lite_{os.getpid()}.db*")

    passed = sum(1 for _, c in results if c)
    total = len(results)
    print(f"\n{'=' * 40}\nPhase 4 search modes: {passed}/{total} checks passed")
    if passed != total:
        print("FAILED:", [n for n, c in results if not c])
        raise SystemExit(1)
    print("ALL PASS")


if __name__ == "__main__":
    asyncio.run(main())
