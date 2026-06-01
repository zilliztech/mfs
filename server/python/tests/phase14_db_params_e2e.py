"""Phase 14 — DB connector parameter combinations (MySQL is the lab):

  - chunk_max truncation: a table with N > chunk_max rows ends up at search_status='partial'
    and only chunk_max chunks land in Milvus.
  - Multiple [[objects]] entries with different `match` patterns + per-pattern field maps.
  - metadata_fields populated correctly on chunks (we don't have search-time filter
    plumbing through the engine API yet, so we assert via the Milvus chunk metadata
    that the fields ARE attached to chunks — the prerequisite for filter pushdown).
  - Empty table: `mfs add` succeeds, 0 chunks, no exception.

Needs OPENAI_API_KEY + local MySQL/MariaDB (127.0.0.1:3306 mfs/mfs/mfstest), same as
phase13_mysql_deep. Lite."""

import asyncio
import os

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []
MY = dict(host="127.0.0.1", port=3306, user="mfs", password="mfs", db="mfstest")


def check(name, cond):
    results.append(bool(cond))
    print(f"  [{OK if cond else FAIL}] {name}")
    return cond


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic")
        raise SystemExit(2)
    import aiomysql

    try:
        conn = await aiomysql.connect(autocommit=True, **MY)
    except Exception as e:  # noqa: BLE001
        print(f"mysql not reachable: {e}")
        raise SystemExit(2)

    suffix = os.getpid()
    big_tbl = f"big_{suffix}"  # >> chunk_max rows -> partial truncation
    small_tbl = f"small_{suffix}"  # patterns-A indexable
    notes_tbl = f"notes_{suffix}"  # patterns-B indexable
    empty_tbl = f"empty_{suffix}"  # 0 rows
    cur = await conn.cursor()
    for t in (big_tbl, small_tbl, notes_tbl, empty_tbl):
        await cur.execute(f"DROP TABLE IF EXISTS `{t}`")
    await cur.execute(f"CREATE TABLE `{big_tbl}` (id INT PRIMARY KEY, body TEXT, kind VARCHAR(20))")
    rows = [
        (i, f"placeholder row {i} - {'odd' if i % 2 else 'even'}", "odd" if i % 2 else "even")
        for i in range(1, 51)
    ]  # 50 rows
    await cur.executemany(f"INSERT INTO `{big_tbl}` (id, body, kind) VALUES (%s, %s, %s)", rows)

    await cur.execute(
        f"CREATE TABLE `{small_tbl}` (id INT PRIMARY KEY, subject TEXT, priority VARCHAR(20))"
    )
    await cur.executemany(
        f"INSERT INTO `{small_tbl}` (id, subject, priority) VALUES (%s, %s, %s)",
        [(1, "ssl certificate expiry alert", "high"), (2, "trivial typo on the about page", "low")],
    )

    await cur.execute(
        f"CREATE TABLE `{notes_tbl}` (note_id INT PRIMARY KEY, content TEXT, author VARCHAR(40))"
    )
    await cur.executemany(
        f"INSERT INTO `{notes_tbl}` (note_id, content, author) VALUES (%s, %s, %s)",
        [
            (101, "kafka consumer lag spiked overnight, restart helped", "alice"),
            (102, "investigating intermittent payment gateway 502s", "bob"),
        ],
    )

    await cur.execute(f"CREATE TABLE `{empty_tbl}` (id INT PRIMARY KEY, body TEXT)")

    base = f"/tmp/mfs_dbparams_{os.getpid()}"
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

    os.environ["MFS_TEST_MYSQL_PW"] = "mfs"
    # ----- 1) chunk_max truncation: cap=10 against the 50-row big table -----
    cfg_a = {
        "host": "127.0.0.1",
        "port": 3306,
        "user": "mfs",
        "database": "mfstest",
        "credential_ref": "env:MFS_TEST_MYSQL_PW",
        "objects": [
            {
                "match": f"/{big_tbl}/rows.jsonl",
                "text_fields": ["body"],
                "locator_fields": ["id"],
                "metadata_fields": ["kind"],
                "chunk_max": 10,
            }
        ],
    }
    # ----- 2) multiple [[objects]] entries with different patterns + per-pattern fields -----
    cfg_b = {
        "host": "127.0.0.1",
        "port": 3306,
        "user": "mfs",
        "database": "mfstest",
        "credential_ref": "env:MFS_TEST_MYSQL_PW",
        "objects": [
            {
                "match": f"/{small_tbl}/rows.jsonl",
                "text_fields": ["subject"],
                "metadata_fields": ["priority"],
                "locator_fields": ["id"],
            },
            {
                "match": f"/{notes_tbl}/rows.jsonl",
                "text_fields": ["content"],
                "metadata_fields": ["author"],
                "locator_fields": ["note_id"],
            },
        ],
    }
    # ----- 3) empty table — exercise the 0-rows path -----
    cfg_c = {
        "host": "127.0.0.1",
        "port": 3306,
        "user": "mfs",
        "database": "mfstest",
        "credential_ref": "env:MFS_TEST_MYSQL_PW",
        "objects": [
            {
                "match": f"/{empty_tbl}/rows.jsonl",
                "text_fields": ["body"],
                "locator_fields": ["id"],
            }
        ],
    }

    conn_uriA = "mysql://chunkmax"
    conn_uriB = "mysql://multiobj"
    conn_uriC = "mysql://emptytbl"
    try:
        eng.milvus.drop_collection("default")
        eng.milvus.ensure_collection("default")

        # === 1) chunk_max truncation ===
        await eng.add(conn_uriA, config=cfg_a)
        cid = (await eng.meta.fetchone("SELECT id FROM connectors WHERE root_uri=?", (conn_uriA,)))[
            "id"
        ]
        ro = await eng.meta.fetchone(
            "SELECT chunk_count, search_status FROM objects WHERE connector_id=? AND object_uri=?",
            (cid, f"/{big_tbl}/rows.jsonl"),
        )
        check(
            f"chunk_max=10 caps chunk_count (got {ro['chunk_count']})",
            ro and ro["chunk_count"] == 10,
        )
        check(
            f"chunk_max truncation flags search_status='partial' (got {ro['search_status']!r})",
            ro and ro["search_status"] == "partial",
        )

        # === 2) multiple [[objects]] entries ===
        await eng.add(conn_uriB, config=cfg_b)
        cidB = (
            await eng.meta.fetchone("SELECT id FROM connectors WHERE root_uri=?", (conn_uriB,))
        )["id"]
        small_ro = await eng.meta.fetchone(
            "SELECT chunk_count FROM objects WHERE connector_id=? AND object_uri=?",
            (cidB, f"/{small_tbl}/rows.jsonl"),
        )
        notes_ro = await eng.meta.fetchone(
            "SELECT chunk_count FROM objects WHERE connector_id=? AND object_uri=?",
            (cidB, f"/{notes_tbl}/rows.jsonl"),
        )
        check(
            f"multi-objects: small table indexed ({small_ro['chunk_count']} chunks)",
            small_ro and small_ro["chunk_count"] == 2,
        )
        check(
            f"multi-objects: notes table indexed ({notes_ro['chunk_count']} chunks)",
            notes_ro and notes_ro["chunk_count"] == 2,
        )
        # cat --locator on each table uses the table-specific locator_field
        small_recd_raw = await eng.cat(conn_uriB + f"/{small_tbl}/rows.jsonl", locator={"id": 1})
        import json as _json

        small_recd = _json.loads(small_recd_raw["content"])
        check(
            "multi-objects: cat --locator on small table by 'id'",
            small_recd.get("id") == 1 and "ssl" in (small_recd.get("subject") or ""),
        )
        notes_recd_raw = await eng.cat(
            conn_uriB + f"/{notes_tbl}/rows.jsonl", locator={"note_id": 101}
        )
        notes_recd = _json.loads(notes_recd_raw["content"])
        check(
            "multi-objects: cat --locator on notes table by 'note_id'",
            notes_recd.get("note_id") == 101 and "kafka" in (notes_recd.get("content") or ""),
        )

        # === metadata_fields attached to row chunks (assert via Milvus chunk metadata) ===
        small_chunks = await asyncio.to_thread(
            eng.milvus.get_chunks_by_object,
            "default",
            conn_uriB,
            conn_uriB + f"/{small_tbl}/rows.jsonl",
        )
        priorities = {(c.get("metadata") or {}).get("priority") for c in small_chunks}
        check(
            f"metadata_fields=['priority'] attached to small chunks (got {priorities})",
            priorities == {"high", "low"},
        )
        notes_chunks = await asyncio.to_thread(
            eng.milvus.get_chunks_by_object,
            "default",
            conn_uriB,
            conn_uriB + f"/{notes_tbl}/rows.jsonl",
        )
        authors = {(c.get("metadata") or {}).get("author") for c in notes_chunks}
        check(
            f"metadata_fields=['author'] attached to notes chunks (got {authors})",
            authors == {"alice", "bob"},
        )

        # === 3) empty table ===
        await eng.add(conn_uriC, config=cfg_c)
        cidC = (
            await eng.meta.fetchone("SELECT id FROM connectors WHERE root_uri=?", (conn_uriC,))
        )["id"]
        empty_ro = await eng.meta.fetchone(
            "SELECT chunk_count, search_status FROM objects WHERE connector_id=? AND object_uri=?",
            (cidC, f"/{empty_tbl}/rows.jsonl"),
        )
        check(
            f"empty table: 0 chunks, no crash (chunks={empty_ro['chunk_count']}, "
            f"status={empty_ro['search_status']!r})",
            empty_ro and empty_ro["chunk_count"] == 0,
        )
    finally:
        try:
            eng.milvus.drop_collection("default")
        except Exception:
            pass
        await eng.shutdown()
        for t in (big_tbl, small_tbl, notes_tbl, empty_tbl):
            await cur.execute(f"DROP TABLE IF EXISTS `{t}`")
        await cur.close()
        conn.close()
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'=' * 46}\n  db params e2e: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
