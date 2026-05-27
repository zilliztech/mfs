"""Phase 13 — directory summary config modes (matrix H1 / H3).

H1: enabled=false -> no directory_summary chunks at all, zero summary API calls.
H3: enabled=true + dir_recursive=false -> only the connector root gets one summary.
Needs OPENAI_API_KEY. Lite.
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


def _tree():
    root = tempfile.mkdtemp(prefix="mfs_smode_")
    os.makedirs(f"{root}/sub", exist_ok=True)
    open(f"{root}/top.md", "w").write("# Top\n\nProject overview and goals.\n")
    open(f"{root}/sub/impl.py", "w").write("def run():\n    return 'subdir implementation'\n")
    return root


async def _dirsum_count(eng):
    return await asyncio.to_thread(eng.milvus.count, "default", 'chunk_kind == "directory_summary"')


def _mkcfg(base):
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"; cfg.milvus.uri = base + "_v.db"; cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"; cfg.transformation_cache.db_path = base + "_t.db"
    return cfg


async def run_case(label, enabled, recursive, expect_count, expect_calls_zero):
    root = _tree()
    base = f"/tmp/mfs_smode_{os.getpid()}_{label}"; os.system(f"rm -rf '{base}'*")
    cfg = _mkcfg(base)
    cfg.summary.enabled = enabled; cfg.summary.dir_recursive = recursive
    eng = Engine(cfg)
    await eng.startup()
    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")
        await eng.add(root)
        n = await _dirsum_count(eng)
        calls = eng.summary.api_calls
        check(f"{label}: directory_summary count == {expect_count}", n == expect_count)
        if expect_calls_zero:
            check(f"{label}: zero summary API calls", calls == 0)
    finally:
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        await eng.shutdown(); shutil.rmtree(root, ignore_errors=True); os.system(f"rm -rf '{base}'*")


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic"); raise SystemExit(2)
    # H1: disabled -> no summaries, no spend
    await run_case("H1-disabled", enabled=False, recursive=True, expect_count=0, expect_calls_zero=True)
    # H3: enabled, non-recursive -> only the root (/) summary
    await run_case("H3-root-only", enabled=True, recursive=False, expect_count=1, expect_calls_zero=False)

    passed = sum(results)
    print(f"\n{'='*46}\n  summary modes: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
