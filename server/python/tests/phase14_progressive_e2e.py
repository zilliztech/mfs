"""Phase 14 — progressive availability + the §6.3 file-priority table.

  - File connector `task_priority` matches design 02-architecture.md §6.3 — we
    classify every bucket with a direct unit-style call against the plugin
    (covers buckets that DEFAULT_IGNORE filters out, like `dist/`, which
    wouldn't otherwise land in the index at all), then re-prove the reachable
    buckets end-to-end via the `object_tasks` table after a real sync.
    Asserts: README/CLAUDE/SKILL/INDEX -> -350, build manifests -> -260,
    src/lib/app -> -220, docs/guides -> -190, tests/fixtures -> +80,
    dist/build/vendor -> +260, everything else -> 0; and ORDER BY priority
    ASC brings README before pyproject before src/ before docs/ before a
    generic file before tests/.

  - dir_summary tasks still ride the same shared queue at priority=-depth.

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
    search_status counts that match the per-object rows."""
import asyncio
import os
import pathlib
import shutil

from mfs_server.config import load_server_config
from mfs_server.connectors.base import ObjectChange
from mfs_server.connectors.file.plugin import FilePlugin
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append(bool(cond)); print(f"  [{OK if cond else FAIL}] {name}"); return cond


def _classify(path: str) -> int:
    """Run the FilePlugin's task_priority on a bare path string. task_priority
    only reads `change.uri` — it doesn't touch self.config/self.ctx/self.root —
    so we bypass __init__ and call the bound method directly. Lets us cover
    DEFAULT_IGNORE'd buckets (dist/, build/, vendor/, node_modules/) that
    would never actually land in the index but whose classification must still
    match §6.3 should the user un-ignore them."""
    plugin = FilePlugin.__new__(FilePlugin)
    return plugin.task_priority(ObjectChange(uri=path, kind="added"))


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic"); raise SystemExit(2)

    # ---- 0. §6.3 priority table — direct, exhaustive classification check ----
    cases = [
        # entrypoints (-350) -- case-insensitive basename
        ("/README.md", -350), ("/readme.md", -350),
        ("/CLAUDE.md", -350), ("/SKILL.md", -350), ("/INDEX.md", -350),
        ("/docs/README.md", -350),                  # basename wins over top-dir
        ("/tests/README.md", -350),                 # basename wins over tests bucket
        # build manifests (-260)
        ("/pyproject.toml", -260), ("/package.json", -260),
        ("/Cargo.toml", -260), ("/go.mod", -260), ("/Makefile", -260),
        ("/packages/x/pyproject.toml", -260),       # manifest anywhere counts
        # core src (-220)
        ("/src/main.py", -220), ("/lib/util.py", -220), ("/app/server.go", -220),
        # docs (-190)
        ("/docs/intro.md", -190), ("/guides/quickstart.md", -190),
        # tests (+80)
        ("/tests/test_auth.py", 80), ("/test/foo.py", 80),
        ("/__tests__/bar.js", 80), ("/fixtures/data.json", 80),
        # generated (+260)
        ("/dist/bundle.js", 260), ("/build/out.o", 260),
        ("/vendor/lib.so", 260), ("/node_modules/pkg/index.js", 260),
        ("/target/release/main", 260), ("/out/foo.js", 260),
        # default (0)
        ("/notes.md", 0), ("/random.py", 0),
        ("/data/metrics.json", 0), ("/scripts/util.sh", 0),
    ]
    for path, want in cases:
        got = _classify(path)
        check(f"§6.3 classify {path:<38s} -> {want:+d} (got {got:+d})", got == want)

    base = f"/tmp/mfs_prog_{os.getpid()}"; os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"; cfg.milvus.uri = base + "_v.db"; cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"; cfg.transformation_cache.db_path = base + "_t.db"
    cfg.summary.enabled = True              # turn on so dir_summary tasks exist
    cfg.summary.include_image_desc = False  # don't VLM here — keeps cost down

    eng = Engine(cfg)
    await eng.startup()

    # ----- fixture: §6.3-covering tree + mixed-kind kinds + DB chunk_max -----
    repo = pathlib.Path(f"{base}_repo")
    repo.mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "docs").mkdir()
    (repo / "tests").mkdir()
    (repo / "data").mkdir()

    # Entry-point doc (priority -350)
    (repo / "README.md").write_text(
        "# Demo repo\n\nThis README is the entrypoint. The SAML SSO module lives in "
        "`src/auth.py`. See `docs/intro.md` for setup.\n")

    # Build manifest (priority -260)
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.0.1"\n')

    # Core source (priority -220) — also drives the kind-routing assertions
    (repo / "src" / "auth.py").write_text(
        '"""Auth module."""\n\n'
        "def verify_saml_sso_assertion(payload):\n"
        '    """Validate the SAML assertion structure and signature."""\n'
        "    return payload.startswith('<saml:Assertion')\n"
        "\n\n"
        "def issue_jwt_for_user(user_id):\n"
        '    """Mint a JWT for the authenticated user."""\n'
        "    return f'jwt-{user_id}'\n")

    # Docs (priority -190)
    (repo / "docs" / "intro.md").write_text(
        "# Intro\n\nWalkthrough of the auth flow. Reference SAML, OIDC.\n")

    # Tests (priority +80)
    (repo / "tests" / "test_auth.py").write_text(
        "def test_verify_saml():\n    assert True\n")

    # Mixed-kind objects (priority 0 each; kind routing covered below)
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

        # ---- 2. §6.3 buckets land in object_tasks for the reachable buckets ----
        # dist/, build/, vendor/, node_modules/ all hit DEFAULT_IGNORE during the
        # file scan and would never reach the task queue — those are covered by
        # the §0 unit-style check above. Here we inspect what actually landed.
        prio_rows = await eng.meta.fetchall(
            "SELECT object_uri, change_kind, priority FROM object_tasks WHERE connector_id=?",
            (cid["id"],))
        by_path = {r["object_uri"]: r for r in prio_rows if r["change_kind"] != "dir_summary"}
        expected_buckets = {
            "/README.md": -350,
            "/pyproject.toml": -260,
            "/src/auth.py": -220,
            "/docs/intro.md": -190,
            "/tests/test_auth.py": 80,
            # generic file in /data with no bucket match -> 0
            "/data/metrics.json": 0,
            "/data/blob.bin": 0,
        }
        for path, want in expected_buckets.items():
            row = by_path.get(path)
            got = row["priority"] if row else None
            check(f"object_tasks: priority({path}) = {want:+d} (got {got})",
                  got == want)

        # ORDER BY priority ASC pulls the §6.3 buckets in the right order. We
        # don't pin the exact tiebreak within a bucket (engine uses started_at
        # which is non-deterministic in a test), so we just assert the
        # bucket-to-bucket relation that drives perceived ordering.
        def _p(path: str) -> int:
            return by_path[path]["priority"]
        check("ORDER BY: README < pyproject", _p("/README.md") < _p("/pyproject.toml"))
        check("ORDER BY: pyproject < src/auth.py",
              _p("/pyproject.toml") < _p("/src/auth.py"))
        check("ORDER BY: src/auth.py < docs/intro.md",
              _p("/src/auth.py") < _p("/docs/intro.md"))
        check("ORDER BY: docs/intro.md < data/metrics.json (generic 0)",
              _p("/docs/intro.md") < _p("/data/metrics.json"))
        check("ORDER BY: data/metrics.json (0) < tests/test_auth.py",
              _p("/data/metrics.json") < _p("/tests/test_auth.py"))

        # dir_summary keeps its own -depth bucket alongside the body buckets.
        dir_prios = [r["priority"] for r in prio_rows if r["change_kind"] == "dir_summary"]
        check(f"dir_summary tasks present and use priority=-depth "
              f"(got {sorted(set(dir_prios))})",
              dir_prios and all(p <= 0 for p in dir_prios) and any(p < 0 for p in dir_prios))

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
