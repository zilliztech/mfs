"""Phase 13 — estimate zero-billing/no-leak (L3/L4) + connector config update (K6/R7.5).
Needs OPENAI_API_KEY. Lite.
"""

import asyncio
import json
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
    root = tempfile.mkdtemp(prefix="mfs_eu_")
    open(f"{root}/a.md", "w").write("# A\n\n" + ("estimate sample paragraph. " * 50))
    open(f"{root}/b.py", "w").write("def f():\n    return 'code'\n")
    base = f"/tmp/mfs_eu_{os.getpid()}"
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
    conn_uri = f"file://local{root}"
    try:
        eng.milvus.drop_collection("default")
        eng.milvus.ensure_collection("default")

        # L3/L4 — estimate: zero embeddings, zero Milvus rows, no persisted state
        calls0 = eng.embed.api_calls
        est = await eng.estimate(root)
        check(
            "estimate returns physical quantities",
            est.get("est_chunks") is not None or est.get("objects") is not None,
        )
        check("L3 estimate: ZERO embedding calls", eng.embed.api_calls == calls0)
        check(
            "L3 estimate: ZERO Milvus chunks",
            await asyncio.to_thread(eng.milvus.count, "default") == 0,
        )
        nconn = await eng.meta.fetchone("SELECT count(*) AS n FROM connectors")
        nobj = await eng.meta.fetchone("SELECT count(*) AS n FROM objects")
        nfs = await eng.meta.fetchone("SELECT count(*) AS n FROM file_state")
        check(
            "L4 estimate: no leaked connector/objects/file_state rows",
            nconn["n"] == 0 and nobj["n"] == 0 and nfs["n"] == 0,
        )

        # K6/R7.5 — config update: register, then update [[objects]] without re-registering
        await eng.add(root, config={"objects": [{"match": "*.secret", "indexable": False}]})
        cid = (await eng.meta.fetchone("SELECT id FROM connectors WHERE root_uri=?", (conn_uri,)))[
            "id"
        ]
        before = json.loads(
            (await eng.meta.fetchone("SELECT config_json FROM connectors WHERE id=?", (cid,)))[
                "config_json"
            ]
        )
        check(
            "update: connector registered with objects config",
            any(o.get("match") == "*.secret" for o in before.get("objects", [])),
        )

        await eng.add(
            root, config={"objects": [{"match": "*.py", "indexable": False}]}, update_config=True
        )
        cid2 = (await eng.meta.fetchone("SELECT id FROM connectors WHERE root_uri=?", (conn_uri,)))[
            "id"
        ]
        after = json.loads(
            (await eng.meta.fetchone("SELECT config_json FROM connectors WHERE id=?", (cid2,)))[
                "config_json"
            ]
        )
        check("K6 update: same connector (not re-registered)", cid2 == cid)
        check(
            "K6 update: config_json reflects new objects rule",
            any(o.get("match") == "*.py" for o in after.get("objects", []))
            and not any(o.get("match") == "*.secret" for o in after.get("objects", [])),
        )
        nconn2 = await eng.meta.fetchone("SELECT count(*) AS n FROM connectors")
        check("K6 update: still exactly one connector", nconn2["n"] == 1)
    finally:
        try:
            eng.milvus.drop_collection("default")
        except Exception:
            pass
        await eng.shutdown()
        shutil.rmtree(root, ignore_errors=True)
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'=' * 46}\n  estimate + config update: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
