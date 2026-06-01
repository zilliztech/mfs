"""Phase 13 — grep regex mode + 0-match (matrix R9.4). Needs OPENAI_API_KEY. Lite."""

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


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic")
        raise SystemExit(2)
    root = tempfile.mkdtemp(prefix="mfs_grepre_")
    open(f"{root}/a.py", "w").write("def alpha():\n    return 1\n\ndef beta_42():\n    return 2\n")
    base = f"/tmp/mfs_grepre_{os.getpid()}"
    os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"
    cfg.milvus.uri = base + "_v.db"
    cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"
    cfg.transformation_cache.db_path = base + "_t.db"
    cfg.summary.enabled = False
    eng = Engine(cfg)
    await eng.startup()
    try:
        eng.milvus.drop_collection("default")
        eng.milvus.ensure_collection("default")
        await eng.add(root)
        # regex: function definitions with a digit in the name
        r = await eng.grep(r"def\s+\w+_\d+", f"{root}/a.py", regex=True)
        check("grep regex matches beta_42", any("beta_42" in (h.get("content") or "") for h in r))
        # plain (non-regex) literal that won't appear -> 0 matches, no crash
        r0 = await eng.grep("ZZZNOPE_absent_literal", f"{root}/a.py", regex=False)
        check("grep absent literal -> 0 matches, no crash", isinstance(r0, list) and len(r0) == 0)
        # regex with no match -> empty
        rn = await eng.grep(r"zzz_nomatch_\d+", f"{root}/a.py", regex=True)
        check("grep regex 0-match -> []", rn == [])
    finally:
        try:
            eng.milvus.drop_collection("default")
        except Exception:
            pass
        await eng.shutdown()
        shutil.rmtree(root, ignore_errors=True)
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'=' * 46}\n  grep regex: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
