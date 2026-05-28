"""Phase 14 — progressive availability + the priority mechanism that already exists.

Covers what's observable today, honestly:

  - The task `priority` ORDER BY mechanism IS in the SQL (engine.py:871
    'ORDER BY priority ASC, started_at ASC'). The ONLY connector that uses it is
    directory_summary which sets `priority=-depth` so deeper folders summarise
    first (bottom-up rollup). We assert that after a sync with summary enabled,
    dir_summary tasks landed with negative priorities and that priority's <=
    body-task priorities (= 0).

  - Mixed-kind file connector — .py (code, embedded) + .json (text_blob, NOT
    embedded by default) + .bin (binary, NOT indexable at all). Each landed
    object must carry the correct `search_status` ('indexed' for code,
    'not_indexed' for text_blob, 'not_indexed' for binary). Search must NOT
    return hits from the text_blob / binary objects. (We deliberately skip
    .png / image here — image goes through the VLM path which is environment-
    dependent; the kind-routing fact this test wants to assert lands cleaner
    on a plain binary.)

  - DB chunk_max truncation produces 'partial' state — already covered for
    objects table in phase14_db_params; here we also assert that hits from
    the 'partial' portion of the index ARE still searchable.

  - Status aggregation — `mfs status` (via engine internals) shows per-
    search_status counts that match the per-object rows.

There is NO file-extension-based priority in the engine today (no connector
overrides `task_priority` beyond returning 0). This test surfaces that
honestly: the hook exists, the SQL respects it, but it's only used for
dir_summary depth. Wiring "index .md before .py before images" would be a
plugin-side override; the priority infrastructure is already ready for it."""
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


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic"); raise SystemExit(2)

    base = f"/tmp/mfs_prog_{os.getpid()}"; os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"; cfg.milvus.uri = base + "_v.db"; cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"; cfg.transformation_cache.db_path = base + "_t.db"
    cfg.summary.enabled = True              # turn on so dir_summary tasks exist
    cfg.summary.include_image_desc = False  # don't VLM here — keeps cost down

    eng = Engine(cfg)
    await eng.startup()

    # ----- fixture: mixed-kind file repo + a DB chunk_max scenario in parallel -----
    repo = pathlib.Path(f"{base}_repo")
    repo.mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "data").mkdir()
    (repo / "src" / "auth.py").write_text(
        '"""Auth module."""\n\n'
        "def verify_saml_sso_assertion(payload):\n"
        '    """Validate the SAML assertion structure and signature."""\n'
        "    return payload.startswith('<saml:Assertion')\n"
        "\n\n"
        "def issue_jwt_for_user(user_id):\n"
        '    """Mint a JWT for the authenticated user."""\n'
        "    return f'jwt-{user_id}'\n")
    (repo / "data" / "metrics.json").write_text(
        '{"endpoint": "auth", "p99_ms": 1240, "qps": 4500}\n')   # text_blob
    # An unknown / non-text extension routes to object_kind="binary":
    # `indexable=False`, so no chunk/embed work runs and the row keeps the
    # default ('not_indexed', chunk_count=0). This is the kind-routing fact
    # we want to pin down — no environment-sensitive VLM path involved.
    (repo / "data" / "blob.bin").write_bytes(bytes(range(256)) * 4)

    # MySQL chunk_max scenario for partial state
    import aiomysql
    try:
        myconn = await aiomysql.connect(
            autocommit=True, host="127.0.0.1", port=3306,
            user="mfs", password="mfs", db="mfstest")
    except Exception as e:  # noqa: BLE001
        print(f"mysql not reachable: {e}"); raise SystemExit(2)
    suffix = os.getpid()
    big_tbl = f"prog_big_{suffix}"
    mycur = await myconn.cursor()
    await mycur.execute(f"DROP TABLE IF EXISTS `{big_tbl}`")
    await mycur.execute(f"CREATE TABLE `{big_tbl}` (id INT PRIMARY KEY, body TEXT)")
    await mycur.executemany(
        f"INSERT INTO `{big_tbl}` (id, body) VALUES (%s, %s)",
        [(i, f"row {i} concerning topic-{i%5}") for i in range(1, 31)])

    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")

        # ---- 1. mixed-kind file connector ----
        await eng.add(str(repo))
        cid = (await eng.meta.fetchone("SELECT id, root_uri FROM connectors WHERE type='file'"))
        uri = cid["root_uri"]
        objs = await eng.meta.fetchall(
            "SELECT object_uri, search_status, chunk_count FROM objects "
            "WHERE connector_id=?", (cid["id"],))
        by_uri = {o["object_uri"]: o for o in objs}
        check(".py code file: search_status='indexed'",
              by_uri.get("/src/auth.py", {}).get("search_status") == "indexed")
        check(".py code file: chunk_count >= 1",
              (by_uri.get("/src/auth.py", {}).get("chunk_count") or 0) >= 1)
        check(".json text_blob: search_status='not_indexed' (matches binary-like treatment)",
              by_uri.get("/data/metrics.json", {}).get("search_status") == "not_indexed")
        check(".json text_blob: chunk_count == 0",
              (by_uri.get("/data/metrics.json", {}).get("chunk_count") or 0) == 0)
        check(".bin binary: search_status='not_indexed'",
              by_uri.get("/data/blob.bin", {}).get("search_status") == "not_indexed")
        check(".bin binary: chunk_count == 0",
              (by_uri.get("/data/blob.bin", {}).get("chunk_count") or 0) == 0)

        # search returns hits only from the code path, NOT from .json / .bin
        res = await eng.search("authenticate SAML SSO", connector_uri=uri,
                               mode="hybrid", top_k=5)
        sources = [r.get("source", "") for r in res]
        check(f"search returns >= 1 hit from .py code file ({len(res)})", len(res) >= 1)
        check("no search hit points at .json (text_blob, not embedded)",
              not any(s.endswith(".json") for s in sources))
        check("no search hit points at .bin (binary, not embedded)",
              not any(s.endswith(".bin") for s in sources))

        # ---- 2. priority mechanism check: dir_summary tasks have priority <= 0 ----
        # After summary.enabled=True + a file sync, the dir_summary phase should have
        # enqueued + processed tasks with `priority = -depth`. Confirm those entries.
        prio_rows = await eng.meta.fetchall(
            "SELECT change_kind, priority FROM object_tasks WHERE connector_id=?",
            (cid["id"],))
        prio_by_kind = {}
        for r in prio_rows:
            prio_by_kind.setdefault(r["change_kind"], []).append(r["priority"])
        body_prios = [p for k, ps in prio_by_kind.items() for p in ps if k != "dir_summary"]
        dir_prios = prio_by_kind.get("dir_summary", [])
        check(f"body tasks all priority=0 (got {set(body_prios) or '∅'})",
              not body_prios or set(body_prios) == {0})
        # depth = len(non-empty path parts); for the root "/" that's 0 -> priority 0,
        # for "/src" or "/data" that's 1 -> priority -1. So we expect <=0 across the
        # board, with at least one strictly-negative entry from a sub-directory.
        check(f"dir_summary tasks have priority=-depth (root=0, subdirs<0) "
              f"(got {sorted(set(dir_prios))})",
              dir_prios and all(p <= 0 for p in dir_prios) and any(p < 0 for p in dir_prios))
        check("dir_summary priorities <= body-task priorities (dir runs first-or-tied in queue)",
              not dir_prios or max(dir_prios) <= min(body_prios or [0]))

        # ---- 3. DB chunk_max truncation produces 'partial' AND search still works ----
        cfg_db = {
            "host": "127.0.0.1", "port": 3306, "user": "mfs", "database": "mfstest",
            "credential_ref": "env:MFS_TEST_MYSQL_PW",
            "objects": [{"match": f"/{big_tbl}/rows.jsonl",
                         "text_fields": ["body"], "locator_fields": ["id"],
                         "chunk_max": 5}],
        }
        os.environ["MFS_TEST_MYSQL_PW"] = "mfs"
        await eng.add("mysql://progressive", config=cfg_db)
        db_cid = (await eng.meta.fetchone(
            "SELECT id FROM connectors WHERE root_uri='mysql://progressive'"))["id"]
        db_obj = await eng.meta.fetchone(
            "SELECT chunk_count, search_status FROM objects WHERE connector_id=? AND object_uri=?",
            (db_cid, f"/{big_tbl}/rows.jsonl"))
        check(f"chunk_max=5 caps DB chunk_count (got {db_obj['chunk_count']})",
              db_obj["chunk_count"] == 5)
        check(f"chunk_max truncation flags search_status='partial' (got {db_obj['search_status']!r})",
              db_obj["search_status"] == "partial")
        # search returns hits from the partial slice — i.e. "partial" doesn't mean unavailable
        db_res = await eng.search("topic-0",
                                  connector_uri="mysql://progressive",
                                  mode="hybrid", top_k=5)
        check(f"search hits from the 'partial' indexed slice ({len(db_res)} hits)",
              len(db_res) >= 1)

        # ---- 4. mfs status aggregation: per-search_status counts match objects table ----
        agg = await eng.meta.fetchall(
            "SELECT search_status, COUNT(*) AS n FROM objects WHERE connector_id=? GROUP BY search_status",
            (cid["id"],))
        agg_d = {r["search_status"]: r["n"] for r in agg}
        check(f"file connector status aggregation: indexed={agg_d.get('indexed', 0)}, "
              f"not_indexed={agg_d.get('not_indexed', 0)}",
              agg_d.get("indexed", 0) >= 1 and agg_d.get("not_indexed", 0) >= 2)
    finally:
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        await eng.shutdown()
        await mycur.execute(f"DROP TABLE IF EXISTS `{big_tbl}`")
        await mycur.close(); myconn.close()
        shutil.rmtree(repo, ignore_errors=True)
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'='*46}\n  progressive e2e: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
