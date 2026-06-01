"""Phase 13 — postgres connector DEEP coverage.

Beyond the basic smoke: schema_summary, cat --locator readback + missing-locator error,
NULL / JSONB columns, and incremental modify/delete (count+cursor fingerprint). Creates
its own table in the live mfstest DB and drops it. Needs OPENAI_API_KEY + local Postgres.
"""

import asyncio
import os

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []
DSN = "postgresql://zhangchen@/mfstest?host=/var/run/postgresql"


def check(name, cond):
    results.append(bool(cond))
    print(f"  [{OK if cond else FAIL}] {name}")
    return cond


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic")
        raise SystemExit(2)
    import asyncpg

    try:
        conn = await asyncpg.connect(DSN)
    except Exception as e:  # noqa: BLE001
        print(f"postgres not reachable: {e}")
        raise SystemExit(2)
    # dedicated schema so the connector indexes ONLY this table (public has unrelated tables)
    sch = f"mfsdeep_{os.getpid()}"
    tbl = "tickets"
    await conn.execute(f'DROP SCHEMA IF EXISTS "{sch}" CASCADE')
    await conn.execute(f'CREATE SCHEMA "{sch}"')
    await conn.execute(f'''CREATE TABLE "{sch}"."{tbl}" (
        id int PRIMARY KEY, subject text, body text, meta jsonb, updated_at timestamptz DEFAULT now())''')
    await conn.executemany(
        f'INSERT INTO "{sch}"."{tbl}" (id, subject, body, meta) VALUES ($1,$2,$3,$4::jsonb)',
        [
            (
                1,
                "SSO login fails",
                "Users bounced back to the identity provider after login",
                '{"sev":1}',
            ),
            (2, "Dark mode toggle", "Add a dark theme switch to the settings page", '{"sev":3}'),
            (3, "Null body row", None, None),
        ],
    )  # NULL body + NULL jsonb

    base = f"/tmp/mfs_pgd_{os.getpid()}"
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
    rows_uri = f"/{sch}/{tbl}/rows.jsonl"
    src = "postgres://deep" + rows_uri
    os.environ["MFS_TEST_PG_DSN"] = (
        DSN  # via credential_ref so reopen (cat/re-add) works after redaction
    )
    cfg_obj = {
        "credential_ref": "env:MFS_TEST_PG_DSN",
        "schemas": [sch],
        "objects": [
            {
                "match": "*rows.jsonl",
                "text_fields": ["subject", "body"],
                "metadata_fields": ["meta"],
                "locator_fields": ["id"],
                "chunk_strategy": "per_row",
            }
        ],
    }
    try:
        eng.milvus.drop_collection("default")
        eng.milvus.ensure_collection("default")
        await eng.add("postgres://deep", config=cfg_obj)
        conn_uri = "postgres://deep"

        cid = (await eng.meta.fetchone("SELECT id FROM connectors WHERE type='postgres'"))["id"]
        ro = await eng.meta.fetchone(
            "SELECT chunk_count FROM objects WHERE connector_id=? AND object_uri=?", (cid, rows_uri)
        )
        check("rows indexed (3 rows incl. NULL row, no crash)", ro and ro["chunk_count"] >= 3)

        # schema_summary
        sc = await asyncio.to_thread(
            eng.milvus.get_chunks_by_object,
            "default",
            conn_uri,
            conn_uri + f"/{sch}/{tbl}/schema.json",
        )
        check(
            "schema_summary chunk produced",
            any(c.get("chunk_kind") == "schema_summary" for c in sc),
        )

        # search + locator
        res = await eng.search(
            "single sign-on bounced to identity provider",
            connector_uri=conn_uri,
            mode="hybrid",
            top_k=5,
        )
        hit = next((e for e in res if e.get("locator")), None)
        check(
            "search hit carries {id} locator",
            hit and isinstance(hit["locator"], dict) and "id" in hit["locator"],
        )

        # cat --locator readback (exact record) + missing-locator error
        import json as _json

        catout = await eng.cat(src, locator={"id": 1})
        recd = _json.loads(catout["content"])
        check(
            "cat --locator returns exact record",
            recd.get("id") == 1 and "identity provider" in (recd.get("body") or ""),
        )
        try:
            await eng.cat(src, locator={"id": 999})
            check("missing locator raises", False)
        except Exception as ex:
            check(
                "missing locator raises",
                "not_found" in str(ex).lower() or "locator" in str(ex).lower(),
            )

        # NULL row searchable by its subject (text built from non-null fields)
        rn = await eng.search("null body row", connector_uri=conn_uri, mode="hybrid", top_k=5)
        check(
            "NULL-column row still indexed/searchable",
            any((e.get("locator") or {}).get("id") == 3 for e in rn),
        )

        # incremental MODIFY: update a row's body + cursor, re-add (no full) -> detected, reflected
        await conn.execute(
            f'UPDATE "{sch}"."{tbl}" SET body=$1, updated_at=now() WHERE id=2',
            "Now supports a high-contrast accessibility palette",
        )
        job = await eng.add("postgres://deep", config=cfg_obj, full=False)
        tk = await eng.meta.fetchall(
            "SELECT change_kind FROM object_tasks WHERE connector_job_id=?", (job,)
        )
        check(
            "incremental modify detected (table re-yielded)",
            any(t["change_kind"] == "modified" for t in tk),
        )
        rm = await eng.search(
            "high-contrast accessibility palette", connector_uri=conn_uri, mode="hybrid", top_k=5
        )
        check(
            "modify reflected in search", any((e.get("locator") or {}).get("id") == 2 for e in rm)
        )

        # incremental DELETE a row -> count changes -> re-index, row gone
        await conn.execute(f'DELETE FROM "{sch}"."{tbl}" WHERE id=1')
        await eng.add("postgres://deep", config=cfg_obj, full=False)
        rd = await eng.search(
            "single sign-on bounced to identity provider",
            connector_uri=conn_uri,
            mode="hybrid",
            top_k=10,
        )
        check(
            "deleted row no longer retrievable",
            not any((e.get("locator") or {}).get("id") == 1 for e in rd),
        )
    finally:
        try:
            eng.milvus.drop_collection("default")
        except Exception:
            pass
        await eng.shutdown()
        await conn.execute(f'DROP SCHEMA IF EXISTS "{sch}" CASCADE')
        await conn.close()
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'=' * 46}\n  postgres deep: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
