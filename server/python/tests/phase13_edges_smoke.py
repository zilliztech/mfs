"""Phase 13 — round-2 edge cases: input / path / search / read (R1, R2, R3, R9). Lite.

Hits the awkward inputs: non-UTF-8 bytes, empty files, missing newline, unicode/quote
filenames, binary, empty dir, add-a-single-file, add-missing-path, empty index, empty
query, top_k bounds, cat-a-dir, out-of-range cat. Needs OPENAI_API_KEY. Lite.
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


async def _obj(eng, conn_uri, rel):
    return await eng.meta.fetchone(
        "SELECT chunk_count, search_status FROM objects o JOIN connectors c ON o.connector_id=c.id "
        "WHERE c.root_uri=? AND o.object_uri=?",
        (conn_uri, rel),
    )


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic")
        raise SystemExit(2)
    root = tempfile.mkdtemp(prefix="mfs_edge_")
    # R1.1 non-UTF-8 bytes
    with open(f"{root}/latin1.md", "wb") as f:
        f.write("# Café\n\nrésumé and naïve façade.\n".encode("latin-1"))
    # R1.2 empty file
    open(f"{root}/empty.md", "w").close()
    # R1.3 no trailing newline
    with open(f"{root}/nonl.txt", "w") as f:
        f.write("alpha\nbeta\ngamma")  # no final newline
    # R1.5 unicode + space filename
    open(f"{root}/résumé 文档.md", "w").write("# Resume\n\nKubernetes operator patterns.\n")
    # R1.6 binary
    with open(f"{root}/blob.bin", "wb") as f:
        f.write(bytes(range(256)) * 8)
    # R3.4 quote in filename (Milvus literal injection surface)
    open(f'{root}/a"b.md', "w").write("# Quote\n\nliteral escaping for storage layer.\n")
    base = f"/tmp/mfs_edge_{os.getpid()}"
    os.system(f"rm -rf '{base}'*")
    eng = Engine(_mkcfg(base))
    await eng.startup()
    conn_uri = f"file://local{root}"
    try:
        eng.milvus.drop_collection("default")
        eng.milvus.ensure_collection("default")

        # R3.1 — search an empty index returns []
        empty_hits = await eng.search("anything", mode="hybrid", top_k=5)
        check("R3.1 search empty index -> []", empty_hits == [])

        await eng.add(root)

        check(
            "R1.1 non-utf8 file indexed (no crash)",
            (await _obj(eng, conn_uri, "/latin1.md")) is not None,
        )
        e = await _obj(eng, conn_uri, "/empty.md")
        check("R1.2 empty file recorded, 0 chunks", e is not None and e["chunk_count"] == 0)
        check(
            "R1.5 unicode/space filename indexed",
            (await _obj(eng, conn_uri, "/résumé 文档.md")) is not None,
        )
        b = await _obj(eng, conn_uri, "/blob.bin")
        check("R1.6 binary recorded but not chunked", b is not None and b["chunk_count"] == 0)

        # R1.3 cat preserves content w/o trailing newline; tail works
        c = await eng.cat(f"{root}/nonl.txt")
        check("R1.3 cat no-trailing-newline exact", c.splitlines() == ["alpha", "beta", "gamma"])
        check(
            "R1.3 tail no-trailing-newline",
            (await eng.tail(f"{root}/nonl.txt", n=2)).splitlines() == ["beta", "gamma"],
        )

        # R3.4 quote-in-name searchable + scope doesn't break (lit escaping)
        rq = await eng.search(
            "literal escaping storage", connector_uri=conn_uri, mode="hybrid", top_k=5
        )
        check(
            "R3.4 quote-filename searchable, scope intact",
            any('a"b.md' in (h["source"] or "") for h in rq),
        )

        # R3.2 empty / whitespace query -> handled, no crash
        try:
            r = await eng.search("   ", mode="hybrid", top_k=3)
            check("R3.2 whitespace query handled (no crash)", isinstance(r, list))
        except Exception as ex:
            check(f"R3.2 whitespace query handled (raised {type(ex).__name__})", False)

        # R3.3 top_k=0 boundary
        try:
            r = await eng.search("storage", connector_uri=conn_uri, mode="hybrid", top_k=0)
            check("R3.3 top_k=0 handled", isinstance(r, list))
        except Exception as ex:
            check(f"R3.3 top_k=0 handled (raised {type(ex).__name__})", False)

        # R9.1 cat a directory -> error
        try:
            await eng.cat(root)
            check("R9.1 cat dir raises", False)
        except IsADirectoryError:
            check("R9.1 cat dir raises IsADirectoryError", True)
        except Exception as ex:
            check(f"R9.1 cat dir raises ({type(ex).__name__})", True)

        # R9.2 cat range out of bounds
        oob = await eng.cat(f"{root}/nonl.txt", range=(100, 200))
        check("R9.2 cat range past EOF -> empty, no crash", oob.strip() == "")

        # R9.3 head/tail n=0
        check("R9.3 head n=0 -> empty", (await eng.head(f"{root}/nonl.txt", n=0)).strip() == "")
        check("R9.3 tail n=0 -> empty", (await eng.tail(f"{root}/nonl.txt", n=0)).strip() == "")
    finally:
        try:
            eng.milvus.drop_collection("default")
        except Exception:
            pass
        await eng.shutdown()
        shutil.rmtree(root, ignore_errors=True)
        os.system(f"rm -rf '{base}'*")

    # R2.5 empty dir add ; R2.2 missing path
    base2 = f"/tmp/mfs_edge2_{os.getpid()}"
    os.system(f"rm -rf '{base2}'*")
    eng2 = Engine(_mkcfg(base2))
    await eng2.startup()
    try:
        eng2.milvus.drop_collection("default")
        eng2.milvus.ensure_collection("default")
        empty_dir = tempfile.mkdtemp(prefix="mfs_emptydir_")
        await eng2.add(empty_dir)
        n = await eng2.meta.fetchone("SELECT count(*) AS n FROM objects")
        check("R2.5 empty dir add -> 0 objects, no crash", n["n"] == 0)
        shutil.rmtree(empty_dir, ignore_errors=True)

        # R2.2 add a path that does not exist
        try:
            await eng2.add("/no/such/path/xyz123")
            # if it doesn't raise, it must at least not leave an active connector with data
            row = await eng2.meta.fetchone("SELECT count(*) AS n FROM objects")
            check("R2.2 missing path -> no objects indexed", row["n"] == 0)
        except Exception:
            check("R2.2 missing path -> clean error", True)
    finally:
        try:
            eng2.milvus.drop_collection("default")
        except Exception:
            pass
        await eng2.shutdown()
        os.system(f"rm -rf '{base2}'*")

    passed = sum(results)
    print(f"\n{'=' * 46}\n  edge cases (R1/R2/R3/R9): {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
