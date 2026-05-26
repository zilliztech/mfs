"""Phase 4 read commands + grep dispatch smoke — needs OPENAI_API_KEY (bash -ic).

ls / cat / cat --range / head / cat --meta, plus grep dispatch: BM25 main path
(indexed document) AND linear-scan fallback (a not_indexed .log text_blob). Lite only
(command logic is backend-agnostic; Phase 8 matrix re-runs on Zilliz).
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


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic")
        raise SystemExit(2)
    root = tempfile.mkdtemp(prefix="mfs_p4c_repo_")
    os.makedirs(f"{root}/src")
    open(f"{root}/auth.md", "w").write("# Session storage\n\nUser sessions live in Redis.\nSecond line here.\nThird line.\n")
    open(f"{root}/src/app.py", "w").write("def login():\n    pass\n")
    open(f"{root}/events.log", "w").write("INFO start\nERROR ERR_TIMEOUT at 12:00\nINFO done\n")

    base = f"/tmp/mfs_p4c_{os.getpid()}"
    os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_meta.db"
    cfg.milvus.uri = base + "_milvus.db"
    cfg.milvus.token = ""
    cfg.object_store.root = base + "_cache"
    cfg.transformation_cache.db_path = base + "_tx.db"
    eng = Engine(cfg)
    await eng.startup()
    try:
        eng.milvus.drop_collection(cfg.namespace)
        eng.milvus.ensure_collection(cfg.namespace)
        await eng.add(root)

        # ls
        ls = (await eng.ls(root))["entries"]
        names = {e["name"] for e in ls}
        check("ls lists auth.md/src/events.log", {"auth.md", "src", "events.log"} <= names)

        # cat full
        text = await eng.cat(f"{root}/auth.md")
        check("cat returns content", "Session storage" in text)
        # cat range (lines 1:2 -> first two lines)
        rng = await eng.cat(f"{root}/auth.md", range=(0, 2))
        check("cat --range 0:2 gives 2 lines", rng.count("\n") <= 2 and "Session storage" in rng and "Third" not in rng)
        # head
        hd = await eng.head(f"{root}/auth.md", n=1)
        check("head -n 1", hd.strip().startswith("# Session"))
        # meta
        m = await eng.cat(f"{root}/auth.md", meta=True)
        check("cat --meta returns media_type", m.get("media_type") == "text/markdown")
        # cat dir -> error
        try:
            await eng.cat(f"{root}/src")
            check("cat dir raises", False)
        except IsADirectoryError:
            check("cat dir raises is_directory", True)

        # grep BM25 main path (indexed document)
        g1 = await eng.grep("session", root)
        check("grep BM25 hits auth.md", any("auth.md" in (r["source"] or "") for r in g1))
        check("grep BM25 via=bm25", any(r["via"] == "bm25" for r in g1))

        # grep linear fallback (events.log is text_blob -> not_indexed)
        g2 = await eng.grep("ERR_TIMEOUT", root)
        lin = [r for r in g2 if r["via"] == "linear"]
        check("grep linear hits events.log", any("events.log" in (r["source"] or "") for r in lin))
        check("grep linear gives line number", any(r["lines"] == [2, 2] for r in lin))
    finally:
        try:
            eng.milvus.drop_collection(cfg.namespace)
        except Exception:
            pass
        await eng.shutdown()
        shutil.rmtree(root, ignore_errors=True)
        os.system(f"rm -rf '{base}'*")

    passed = sum(1 for _, c in results if c)
    total = len(results)
    print(f"\n{'='*40}\nPhase 4 commands: {passed}/{total} checks passed")
    if passed != total:
        print("FAILED:", [n for n, c in results if not c])
        raise SystemExit(1)
    print("ALL PASS")


if __name__ == "__main__":
    asyncio.run(main())
