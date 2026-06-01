"""Phase 11 — recursive directory summaries.

With summary.enabled the engine produces NO per-file summary (neither for documents nor
code) and instead, after the file-index phase, builds one directory_summary per directory
bottom-up: a parent folds in its children's summaries plus its own files' content. Runs
against the real file connector over a temp tree. Needs OPENAI_API_KEY (bash -ic). Lite.
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


def _build_tree(root):
    os.makedirs(f"{root}/src/auth", exist_ok=True)
    open(f"{root}/README.md", "w").write(
        "# Billing Service\n\nHandles invoices, subscriptions and payment retries.\n"
    )
    open(f"{root}/src/server.py", "w").write(
        "def charge(invoice):\n    # capture payment for an invoice\n    return gateway.capture(invoice)\n"
    )
    open(f"{root}/src/auth/login.py", "w").write(
        "def login(user, password):\n    # verify credentials and issue a session token\n    return issue_token(user)\n"
    )


async def _dirsum_count(eng):
    return await asyncio.to_thread(eng.milvus.count, "default", 'chunk_kind == "directory_summary"')


async def _kind_set(eng, q):
    res = await eng.search(q, mode="hybrid", top_k=10)
    return {e.get("metadata", {}).get("chunk_kind") for e in res}


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic")
        raise SystemExit(2)
    root = tempfile.mkdtemp(prefix="mfs_sum_root_")
    _build_tree(root)
    base = f"/tmp/mfs_sum_{os.getpid()}"
    os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"
    cfg.milvus.uri = base + "_v.db"
    cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"
    cfg.transformation_cache.db_path = base + "_t.db"
    cfg.summary.enabled = True
    eng = Engine(cfg)
    await eng.startup()
    try:
        eng.milvus.drop_collection("default")
        eng.milvus.ensure_collection("default")
        await eng.add(root)

        # 1. no per-file summary survives for either documents or code
        all_kinds = await _kind_set(eng, "billing invoices payment login credentials session")
        check(
            "files yield body only (no per-file summary)",
            "body" in all_kinds and "summary" not in all_kinds,
        )

        # 2. one directory_summary per directory: root, /src, /src/auth
        n_dirs = await _dirsum_count(eng)
        check("directory_summary per directory (root + /src + /src/auth = 3)", n_dirs == 3)

        # 3. a directory-level query retrieves a directory_summary
        dir_kinds = await _kind_set(eng, "what does this service directory contain overall")
        check("directory query hits directory_summary", "directory_summary" in dir_kinds)

        # 4. recursion rolls up: the root summary text mentions a concept that only lives in
        #    a nested file (auth/login), proving child content reached the top via roll-up.
        conn_uri = f"file://local{root}"
        root_chunks = await asyncio.to_thread(
            eng.milvus.get_chunks_by_object, "default", conn_uri, conn_uri + "/"
        )
        root_txt = " ".join(
            c["content"] for c in root_chunks if c.get("chunk_kind") == "directory_summary"
        ).lower()
        check(
            "root summary rolls up nested content (mentions auth/login/session)",
            any(w in root_txt for w in ("auth", "login", "credential", "session")),
        )

        # 5. summary cache: a full re-index makes zero new summary API calls
        calls = eng.summary.api_calls
        await eng.add(root, full=True)
        check("re-index hits summary cache (0 new summary calls)", eng.summary.api_calls == calls)
    finally:
        try:
            eng.milvus.drop_collection("default")
        except Exception:
            pass
        await eng.shutdown()
        shutil.rmtree(root, ignore_errors=True)
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'=' * 46}\n  directory summaries: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
