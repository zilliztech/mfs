"""Phase 13 — read commands + scale edges (matrix E3 / E4 / M1 / M3 / M4). Lite.

  M1 cat --range   -> exact line slice [start, end)
  E3 tail          -> last n lines
  E4 export        -> full content, no cap
  M3 chunk_max     -> object over the chunk cap is marked search_status='partial'
  M4 grep cap      -> linear scan over not-indexed files is capped with a notice
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
    results.append(bool(cond))
    print(f"  [{OK if cond else FAIL}] {name}")
    return cond


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic")
        raise SystemExit(2)
    root = tempfile.mkdtemp(prefix="mfs_rc_")
    lines = "\n".join(f"line{i}" for i in range(10)) + "\n"
    open(f"{root}/lines.txt", "w").write(lines)
    # a doc large enough to split into many chunks (for chunk_max=partial)
    open(f"{root}/big.md", "w").write(
        "# Big\n\n" + ("Storage caching paragraph. " * 80 + "\n\n") * 10
    )
    # > _GREP_LINEAR_SCAN_MAX (200) not-indexed .log files (text_blob: not embedded)
    logdir = f"{root}/logs"
    os.makedirs(logdir, exist_ok=True)
    for i in range(205):
        open(f"{logdir}/app{i}.log", "w").write(f"event NEEDLE occurred in shard {i}\n")
    base = f"/tmp/mfs_rc_{os.getpid()}"
    os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"
    cfg.milvus.uri = base + "_v.db"
    cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"
    cfg.transformation_cache.db_path = base + "_t.db"
    cfg.summary.enabled = False
    cfg.chunk.default_chunk_max = 1  # force the big doc to be 'partial'
    eng = Engine(cfg)
    await eng.startup()
    conn_uri = f"file://local{root}"
    try:
        eng.milvus.drop_collection("default")
        eng.milvus.ensure_collection("default")
        await eng.add(root)

        # M1 cat --range [2,5) -> line2,line3,line4
        sl = await eng.cat(f"{root}/lines.txt", range=(2, 5))
        check("M1 cat --range slices [2,5)", sl.splitlines() == ["line2", "line3", "line4"])

        # E3 tail
        tl = await eng.tail(f"{root}/lines.txt", n=2)
        check("E3 tail -n2 -> last two lines", tl.splitlines() == ["line8", "line9"])

        # E4 export -> full content
        ex = await eng.export(f"{root}/lines.txt")
        check("E4 export returns full content", ex.rstrip("\n") == lines.rstrip("\n"))

        # M3 chunk_max -> partial
        obj = await eng.meta.fetchone(
            "SELECT search_status FROM objects o JOIN connectors c ON o.connector_id=c.id "
            "WHERE c.root_uri=? AND o.object_uri='/big.md'",
            (conn_uri,),
        )
        check(
            "M3 over chunk_max -> search_status='partial'",
            obj and obj["search_status"] == "partial",
        )

        # M4 grep linear-scan cap notice
        hits = await eng.grep("NEEDLE", f"{root}/logs", top_k=500)
        joined = " ".join((h.get("content") or "") for h in hits)
        check("M4 grep linear scan capped with a notice", "capped" in joined)
    finally:
        try:
            eng.milvus.drop_collection("default")
        except Exception:
            pass
        await eng.shutdown()
        shutil.rmtree(root, ignore_errors=True)
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'=' * 46}\n  read commands + scale edges: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
