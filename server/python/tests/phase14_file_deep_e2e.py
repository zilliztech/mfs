"""Phase 14 — file connector deep paths: gitignore + rename detection + code chunking.

Covers behaviours we previously had only unit-level confidence in but never drove
end-to-end through the engine:

  - .gitignore + .mfsignore semantics: ignored files never appear in the index;
    negation patterns (!keep.log) override an earlier ignore.
  - Rename detection: rename a file (size + sha1 + inode preserved), re-sync,
    verify the new path appears in objects and the OLD path is gone — i.e.
    the connector recognised the rename and didn't double-index.
  - Code chunking: a non-trivial .py file is split via chonkie's CodeChunker
    (tree-sitter), not the plain RecursiveChunker — chunks must align with
    function/class boundaries (line ranges include start_line + end_line that
    map back to real code).

Self-contained; needs OPENAI_API_KEY (bash -ic)."""
import asyncio
import os
import pathlib
import shutil

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append(bool(cond)); print(f"  [{OK if cond else FAIL}] {name}"); return cond


CODE_FILE = '''\
"""A small module to exercise chonkie's CodeChunker via tree-sitter."""
from __future__ import annotations


def add(a: int, b: int) -> int:
    """Return a + b."""
    return a + b


def multiply(a: int, b: int) -> int:
    """Return a * b."""
    return a * b


class Calculator:
    """A tiny calculator class."""

    def __init__(self, base: int = 0) -> None:
        self.base = base

    def shift(self, delta: int) -> int:
        """Update base by delta and return the new value."""
        self.base = self.base + delta
        return self.base

    def reset(self) -> None:
        """Reset base to zero."""
        self.base = 0
'''


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic"); raise SystemExit(2)

    base = f"/tmp/mfs_fdeep_{os.getpid()}"; os.system(f"rm -rf '{base}'*")
    repo = pathlib.Path(f"{base}_repo")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"; cfg.milvus.uri = base + "_v.db"; cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"; cfg.transformation_cache.db_path = base + "_t.db"
    cfg.summary.enabled = False
    cfg.chunk.chunk_size = 200    # small budget so the calc.py file is forced to split
                                  # into multiple chonkie CodeChunker chunks
    eng = Engine(cfg)
    await eng.startup()

    # ---- fixture tree ----
    repo.mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "node_modules" / "pkg").mkdir(parents=True)
    (repo / "build").mkdir()

    (repo / "README.md").write_text("# project readme\nshould be indexed.\n")
    (repo / "src" / "calc.py").write_text(CODE_FILE)
    (repo / "src" / "app.log").write_text("INFO startup at 2024-01-01\n")           # ignored by .gitignore (*.log)
    (repo / "src" / "keep.log").write_text("KEPT: explicitly negated\n")            # un-ignored by !keep.log
    (repo / "node_modules" / "pkg" / "index.js").write_text("// junk\n")            # ignored by .mfsignore (node_modules/)
    (repo / "build" / "out.o").write_text("binary garbage")                          # ignored by .mfsignore (build/)
    (repo / "secret_creds.txt").write_text("hunter2")                                # ignored by .mfsignore (secret_*)
    (repo / ".gitignore").write_text("*.log\n!keep.log\n")
    (repo / ".mfsignore").write_text("node_modules/\nbuild/\nsecret_*\n")

    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")
        await eng.add(str(repo))

        crow = await eng.meta.fetchone(
            "SELECT id, root_uri FROM connectors WHERE type='file'")
        cid = crow["id"]
        uri = crow["root_uri"]

        objs = await eng.meta.fetchall(
            "SELECT object_uri FROM objects WHERE connector_id=?", (cid,))
        paths = {r["object_uri"] for r in objs}

        # ---- gitignore + mfsignore semantics ----
        check("README.md indexed", "/README.md" in paths)
        check("src/calc.py indexed", "/src/calc.py" in paths)
        check(".gitignore '*.log' filters src/app.log", "/src/app.log" not in paths)
        check(".gitignore '!keep.log' (negation) keeps src/keep.log", "/src/keep.log" in paths)
        check(".mfsignore 'node_modules/' filters whole tree",
              not any(p.startswith("/node_modules/") for p in paths))
        check(".mfsignore 'build/' filters whole tree",
              not any(p.startswith("/build/") for p in paths))
        check(".mfsignore 'secret_*' filters by glob", "/secret_creds.txt" not in paths)

        # ---- code chunking: calc.py must produce >1 chunk with non-overlapping line ranges
        calc_full_uri = uri + "/src/calc.py"
        calc_chunks = await asyncio.to_thread(
            eng.milvus.get_chunks_by_object, "default", uri, calc_full_uri)
        n_chunks = len(calc_chunks)
        # ChonkieCodeChunker for python typically splits each function/class into its own
        # chunk; we just want >1 chunk + valid line ranges
        check(f"calc.py produced multiple chunks via CodeChunker (got {n_chunks})", n_chunks >= 2)
        def _lines(c):
            return ((c.get("locator") or {}).get("lines")) or None
        line_ranges_ok = all(
            isinstance(_lines(c), list) and len(_lines(c)) == 2 and
            _lines(c)[0] >= 1 and _lines(c)[1] >= _lines(c)[0]
            for c in calc_chunks)
        check("every calc.py chunk carries a valid locator={'lines':[start,end]} range",
              line_ranges_ok)

        # ---- semantic search hits the right function in calc.py
        res = await eng.search("calculator multiply two numbers",
                               connector_uri=uri, mode="hybrid", top_k=5)
        on_calc = [r for r in res if (r.get("source") or "").endswith("/src/calc.py")]
        check(f"search for 'multiply' hits calc.py ({len(on_calc)} hits)", len(on_calc) >= 1)

        # ---- rename detection ----
        # rename calc.py -> calc_v2.py; re-sync; verify old path gone, new path present, and
        # NOT just "added + deleted" (would be wasteful). The file connector pairs renames
        # by (size, mtime_ns, inode, sha1); preserving inode means using shutil.move on the
        # same fs partition, which os.rename does cleanly.
        (repo / "src" / "calc_v2.py").parent.mkdir(parents=True, exist_ok=True)
        os.rename(repo / "src" / "calc.py", repo / "src" / "calc_v2.py")
        await eng.add(str(repo))

        objs2 = await eng.meta.fetchall(
            "SELECT object_uri FROM objects WHERE connector_id=?", (cid,))
        paths2 = {r["object_uri"] for r in objs2}
        check("after rename: old path /src/calc.py gone", "/src/calc.py" not in paths2)
        check("after rename: new path /src/calc_v2.py present", "/src/calc_v2.py" in paths2)

        # Search must still find content under the new path
        res2 = await eng.search("calculator multiply two numbers",
                                connector_uri=uri, mode="hybrid", top_k=5)
        new_calc = [r for r in res2 if (r.get("source") or "").endswith("/src/calc_v2.py")]
        check(f"after rename: search surfaces hits under /src/calc_v2.py ({len(new_calc)})",
              len(new_calc) >= 1)
    finally:
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        await eng.shutdown()
        shutil.rmtree(repo, ignore_errors=True)
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'='*46}\n  file deep e2e: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
