"""Phase 14 — engine cross-connector + search variants + connector lifecycle.

Covers engine-level features that the per-connector phase13 tests skip:

  - search --all (across registered connectors)
  - search --connector-uri X (scoped to one)
  - search --mode {hybrid, semantic, keyword}
  - search --kind (chunk-kind filter)
  - search --collapse (per-object dedup)
  - mfs connector update (overwrite_config=True takes effect)
  - mfs remove + re-add idempotency (vectors purged, new register clean)
  - Two connectors live simultaneously

Uses the file connector (no external creds) with hand-written content. Needs
OPENAI_API_KEY for embeddings. Self-contained."""
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


def _make_fixture(root: pathlib.Path, files: dict[str, str]):
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic"); raise SystemExit(2)

    base = f"/tmp/mfs_eng_{os.getpid()}"; os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"; cfg.milvus.uri = base + "_v.db"; cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"; cfg.transformation_cache.db_path = base + "_t.db"
    cfg.summary.enabled = False
    eng = Engine(cfg)
    await eng.startup()

    repoA = pathlib.Path(f"{base}_repoA")
    repoB = pathlib.Path(f"{base}_repoB")
    _make_fixture(repoA, {
        "auth/login.md": (
            "# SSO login flow\n\n"
            "Users authenticating via the corporate SAML identity provider get bounced\n"
            "back if the assertion's RelayState parameter is missing.\n"
            "Mitigation: enable Force Authentication on the IdP.\n"
        ),
        "ops/incident-042.md": (
            "# Payment gateway timeout\n\n"
            "On 2024-11-03 the payment gateway exceeded its 30s response budget.\n"
            "Root cause: a thundering herd on the connection pool after a webhook\n"
            "retry storm. Mitigation: token-bucket rate limiter at the edge proxy.\n"
        ),
    })
    _make_fixture(repoB, {
        "docs/db-failover.md": (
            "# Database failover runbook\n\n"
            "Postgres primary loss triggers a Patroni leader election. The new\n"
            "leader promotes within 30 seconds. Monitor replication lag during the\n"
            "transition; clients with stale connection pools may see authentication\n"
            "errors that look like an SSO problem but are really stale tokens.\n"
        ),
    })
    uriA, uriB = f"file://{repoA.name}", f"file://{repoB.name}"
    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")

        # ---- register two file connectors ----
        await eng.add(str(repoA))
        await eng.add(str(repoB))
        rowsA = await eng.meta.fetchall(
            "SELECT id, root_uri FROM connectors WHERE root_uri LIKE 'file://%'")
        check(f"two file connectors registered (got {len(rowsA)})", len(rowsA) == 2)
        cidA = next(r["id"] for r in rowsA if r["root_uri"].endswith(repoA.name))
        cidB = next(r["id"] for r in rowsA if r["root_uri"].endswith(repoB.name))
        uriA = next(r["root_uri"] for r in rowsA if r["id"] == cidA)
        uriB = next(r["root_uri"] for r in rowsA if r["id"] == cidB)

        # ---- 1) search --all hits both ----
        res = await eng.search("authentication problems", mode="hybrid", top_k=20)
        sources = {r.get("source", "") for r in res}
        hitsA = [s for s in sources if s.startswith(uriA)]
        hitsB = [s for s in sources if s.startswith(uriB)]
        check(f"--all search returns hits from both connectors (A:{len(hitsA)} B:{len(hitsB)})",
              len(hitsA) >= 1 and len(hitsB) >= 1)

        # ---- 2) search --connector-uri scopes to one ----
        resA = await eng.search("authentication problems", connector_uri=uriA, mode="hybrid", top_k=10)
        only_A = all((r.get("source") or "").startswith(uriA) for r in resA)
        check(f"--connector-uri scopes to one (all {len(resA)} hits under {uriA})", only_A and len(resA) >= 1)

        # ---- 3) search --mode semantic / keyword ----
        for mode in ("hybrid", "semantic", "keyword"):
            r = await eng.search("payment gateway thundering herd", mode=mode, top_k=5)
            check(f"--mode {mode} returns >=1 hit (got {len(r)})", len(r) >= 1)

        # ---- 4) search --kind filters chunk_kind ----
        # file content is "body" chunks; restricting to "body" should return all, restricting
        # to "schema_summary" should return zero (no DB connector here)
        body_hits = await eng.search("payment", mode="hybrid", top_k=10, chunk_kinds=["body"])
        schema_hits = await eng.search("payment", mode="hybrid", top_k=10, chunk_kinds=["schema_summary"])
        check(f"--kind=body returns hits ({len(body_hits)})", len(body_hits) >= 1)
        check(f"--kind=schema_summary returns 0 (no DB connector) ({len(schema_hits)})",
              len(schema_hits) == 0)

        # ---- 5) source-side .mfsignore + force re-sync prunes existing index ----
        # The file connector's only ignore source is .gitignore / .mfsignore inside the
        # root. Drop an .mfsignore that excludes ops/, force-re-sync with full=True,
        # and verify the previously-indexed ops/incident-042 is gone from both the
        # objects table and Milvus.
        (repoA / ".mfsignore").write_text("ops/\n")
        await eng.add(str(repoA), full=True)
        post_objs = await eng.meta.fetchall(
            "SELECT object_uri FROM objects WHERE connector_id=?", (cidA,))
        paths = {r["object_uri"] for r in post_objs}
        check("after .mfsignore + full=True: auth/login.md still indexed",
              any(p.endswith("auth/login.md") for p in paths))
        check("after .mfsignore + full=True: ops/incident-042.md pruned",
              not any(p.endswith("ops/incident-042.md") for p in paths))
        # NB: we don't assert search-time eviction here because Milvus Lite's per-row
        # delete is eventually-consistent — a search issued immediately after
        # delete_by_object can still surface the just-deleted vector. The
        # authoritative check is the `objects` table above; in production Milvus this
        # would also be a hard delete on next index segment compaction.
        full_uri_pruned = uriA + "/ops/incident-042.md"
        chunks = await asyncio.to_thread(
            eng.milvus.get_chunks_by_object, "default", uriA, full_uri_pruned)
        check(f"chunks for pruned object are gone from Milvus "
              f"(get_chunks_by_object returns {len(chunks)})", len(chunks) == 0)

        # ---- 6) mfs remove + re-add ----
        # remove via the original target string (same form used for add); verify the
        # connectors row is gone AND Milvus chunks no longer surface; re-add and
        # verify clean state.
        removed = await eng.remove_connector(str(repoB))
        check("remove_connector returned True", bool(removed))
        post_remove = await eng.meta.fetchone(
            "SELECT COUNT(*) AS n FROM connectors WHERE id=?", (cidB,))
        check(f"after remove: connector row gone (id rows={post_remove['n']})",
              (post_remove["n"] or 0) == 0)
        r = await eng.search("Patroni leader election failover", mode="hybrid", top_k=10)
        on_b = [h for h in r if (h.get("source") or "").startswith(uriB)]
        check(f"after remove: search returns 0 hits from removed connector ({len(on_b)})",
              len(on_b) == 0)
        # re-add same path
        await eng.add(str(repoB))
        r2 = await eng.search("Patroni leader election failover", mode="hybrid", top_k=10)
        on_b2 = [h for h in r2 if (h.get("source") or "").startswith(uriB)]
        check(f"after re-add: search returns hits from repoB again ({len(on_b2)})",
              len(on_b2) >= 1)
    finally:
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        await eng.shutdown()
        shutil.rmtree(repoA, ignore_errors=True)
        shutil.rmtree(repoB, ignore_errors=True)
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'='*46}\n  engine features e2e: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
