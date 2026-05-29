"""Phase 14 — rename detection and the "zero re-embed" promise.

The file connector pairs added×deleted by (inode, size) and falls back to sha1
so a content-identical move is detected as `renamed`, letting the engine reuse
the existing chunk dense_vec — just rewriting chunk_id under the new URI. This
test covers each branch of that contract end-to-end:

  T1. Intra-dir rename (os.rename keeps inode)         — inode pair, vector reuse
  T2. Cross-dir rename                                  — inode pair, vector reuse
                                                          across path prefix
  T3. Rename + content change (sha1 differs, size too) — no pair, add+delete,
                                                          re-embed
  T4. sha1-fallback (cp+rm: new inode, same content)   — sha1 pair, vector reuse
  T5. Multiple parallel renames in one sync            — both pair independently,
                                                          third file untouched
  T6. Artifact migration on HTML rename                — converted_md follows
                                                          the rename; also
                                                          surfaces the artifact_cache
                                                          table-row gap (see note)
  T7. §6.3 priority follows the NEW path bucket        — src/foo.py (-220) ->
                                                          tests/foo.py (+80)
  T8. Chained rename a -> a2 -> a3                     — each step pairs, total
                                                          embedding API calls
                                                          stay at the initial cost

Observable invariants: `eng.embed.api_calls` deltas count vector reuse;
`object_tasks` rows pin the change_kind / priority the framework wrote; Milvus
chunk_id is recomputed under the new URI and content/dense_vec must match the
old chunks pre-rename. We snapshot before/after each case.

Self-contained; needs OPENAI_API_KEY (bash -ic)."""
import asyncio
import os
import pathlib
import shutil
import time

from mfs_server.config import load_server_config
from mfs_server.engine.engine import Engine
from mfs_server.storage.ids import chunk_id

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append(bool(cond)); print(f"  [{OK if cond else FAIL}] {name}"); return cond


PY_BODY = (
    '"""Auth module."""\n'
    "from __future__ import annotations\n\n"
    "def verify_saml_sso_assertion(payload):\n"
    '    """Validate the SAML assertion structure and signature."""\n'
    "    return payload.startswith('<saml:Assertion')\n\n"
    "def issue_jwt_for_user(user_id):\n"
    '    """Mint a JWT for the authenticated user."""\n'
    "    return f'jwt-{user_id}'\n\n"
    "class TokenStore:\n"
    "    def __init__(self) -> None:\n"
    "        self._tokens: dict[str, str] = {}\n"
    "    def put(self, key: str, value: str) -> None:\n"
    "        self._tokens[key] = value\n"
    "    def get(self, key: str) -> str | None:\n"
    "        return self._tokens.get(key)\n"
)


async def _objs(eng: Engine, cid: str) -> dict[str, dict]:
    rows = await eng.meta.fetchall(
        "SELECT object_uri, fingerprint, chunk_count, search_status "
        "FROM objects WHERE connector_id=?", (cid,))
    return {r["object_uri"]: r for r in rows}


async def _tasks(eng: Engine, cid: str) -> list[dict]:
    rows = await eng.meta.fetchall(
        "SELECT object_uri, old_uri, change_kind, priority, status FROM object_tasks "
        "WHERE connector_id=? ORDER BY started_at DESC", (cid,))
    return rows


async def _cid_for(eng: Engine, root_uri: str) -> str:
    row = await eng.meta.fetchone("SELECT id FROM connectors WHERE root_uri=?", (root_uri,))
    return row["id"] if row else None


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic"); raise SystemExit(2)

    base = f"/tmp/mfs_rename_{os.getpid()}"; os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"; cfg.milvus.uri = base + "_v.db"; cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"; cfg.transformation_cache.db_path = base + "_t.db"
    cfg.summary.enabled = False
    cfg.chunk.chunk_size = 200    # small so PY_BODY splits into >=2 chunks

    eng = Engine(cfg)
    await eng.startup()
    ns = eng.ns
    tmp = pathlib.Path(f"{base}_work"); tmp.mkdir()

    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")

        # =====================================================
        # T1 — intra-dir rename, inode preserved
        # =====================================================
        print("\n--- T1 · intra-dir rename (os.rename preserves inode) ---")
        repo1 = tmp / "t1"; (repo1 / "src").mkdir(parents=True)
        (repo1 / "src" / "auth.py").write_text(PY_BODY)
        eng.embed.api_calls = 0
        await eng.add(str(repo1))
        cid1 = await _cid_for(eng, f"file://local{repo1}")
        uri1 = f"file://local{repo1}"
        before_calls = eng.embed.api_calls
        before_objs = await _objs(eng, cid1)
        old_chunks = await asyncio.to_thread(
            eng.milvus.get_chunks_by_object, "default", uri1, uri1 + "/src/auth.py")
        check(f"T1 initial sync embedded auth.py ({before_calls} api calls, "
              f"{len(old_chunks)} chunks)",
              before_calls >= 1 and len(old_chunks) >= 2)

        os.rename(repo1 / "src" / "auth.py", repo1 / "src" / "auth_v2.py")
        eng.embed.api_calls = 0      # zero out, count only what the rename step costs
        await eng.add(str(repo1))
        after_objs = await _objs(eng, cid1)
        new_chunks = await asyncio.to_thread(
            eng.milvus.get_chunks_by_object, "default", uri1, uri1 + "/src/auth_v2.py")
        old_chunks_after = await asyncio.to_thread(
            eng.milvus.get_chunks_by_object, "default", uri1, uri1 + "/src/auth.py")
        # The rename branch yields a 'renamed' task — find it
        tasks_t1 = await _tasks(eng, cid1)
        renamed_t1 = [t for t in tasks_t1 if t["change_kind"] == "renamed"]
        check(f"T1 sync yielded exactly one 'renamed' task ({len(renamed_t1)})",
              len(renamed_t1) == 1)
        check("T1 renamed task points at new_uri AND old_uri",
              renamed_t1 and renamed_t1[0]["object_uri"] == "/src/auth_v2.py"
              and renamed_t1[0]["old_uri"] == "/src/auth.py")
        check("T1 objects table: old path gone, new path present",
              "/src/auth.py" not in after_objs and "/src/auth_v2.py" in after_objs)
        check(f"T1 chunk_count preserved across rename "
              f"({before_objs['/src/auth.py']['chunk_count']} -> "
              f"{after_objs['/src/auth_v2.py']['chunk_count']})",
              before_objs["/src/auth.py"]["chunk_count"]
              == after_objs["/src/auth_v2.py"]["chunk_count"])
        check(f"T1 zero embedding API calls during rename "
              f"(delta={eng.embed.api_calls})", eng.embed.api_calls == 0)
        check(f"T1 Milvus has chunks at new uri, none at old "
              f"(new={len(new_chunks)}, old={len(old_chunks_after)})",
              len(new_chunks) == len(old_chunks) and len(old_chunks_after) == 0)

        # chunk_id and dense_vec invariants:
        #   - chunk_id MUST change (new uri folded into the hash)
        #   - content + dense_vec MUST match what we had on old chunks
        old_by_lines = {tuple(c["lines"] or []): c for c in old_chunks}
        new_by_lines = {tuple(c["lines"] or []): c for c in new_chunks}
        check("T1 each new chunk pairs with an old chunk by line range",
              set(old_by_lines) == set(new_by_lines))
        chunk_id_changed_all = all(
            old_by_lines[k]["chunk_id"] != new_by_lines[k]["chunk_id"]
            for k in old_by_lines)
        check("T1 chunk_id rewritten (new uri folded into the hash)",
              chunk_id_changed_all)
        # recompute the expected chunk_id and compare to what landed
        expected_id = chunk_id(ns, uri1, uri1 + "/src/auth_v2.py", "body", None,
                                next(iter(new_by_lines)))
        check("T1 new chunk_id matches sha1(ns + connector_uri + new_uri + ...)",
              expected_id in {c["chunk_id"] for c in new_chunks})
        content_preserved = all(
            old_by_lines[k]["content"] == new_by_lines[k]["content"]
            for k in old_by_lines)
        check("T1 chunk content is byte-identical post-rename", content_preserved)
        vec_preserved = all(
            list(old_by_lines[k]["dense_vec"]) == list(new_by_lines[k]["dense_vec"])
            for k in old_by_lines)
        check("T1 dense_vec preserved verbatim — vectors are reused, not recomputed",
              vec_preserved)

        # =====================================================
        # T2 — cross-dir rename
        # =====================================================
        print("\n--- T2 · cross-directory rename (src/ -> lib/) ---")
        (repo1 / "lib").mkdir()
        os.rename(repo1 / "src" / "auth_v2.py", repo1 / "lib" / "auth_v2.py")
        eng.embed.api_calls = 0
        await eng.add(str(repo1))
        objs_t2 = await _objs(eng, cid1)
        tasks_t2 = await _tasks(eng, cid1)
        renamed_t2 = [t for t in tasks_t2
                      if t["change_kind"] == "renamed" and t["object_uri"] == "/lib/auth_v2.py"]
        check("T2 yielded 'renamed' with /src/auth_v2.py -> /lib/auth_v2.py",
              len(renamed_t2) == 1 and renamed_t2[0]["old_uri"] == "/src/auth_v2.py")
        check("T2 old path removed, new path indexed",
              "/src/auth_v2.py" not in objs_t2 and "/lib/auth_v2.py" in objs_t2)
        check(f"T2 zero embedding API calls "
              f"(delta={eng.embed.api_calls})", eng.embed.api_calls == 0)
        chunks_t2 = await asyncio.to_thread(
            eng.milvus.get_chunks_by_object, "default", uri1, uri1 + "/lib/auth_v2.py")
        check(f"T2 Milvus chunks at the new path ({len(chunks_t2)})",
              len(chunks_t2) == len(new_chunks))

        # =====================================================
        # T3 — rename + content change (no pair, add+delete, re-embed)
        # =====================================================
        print("\n--- T3 · rename + content change (no pairing) ---")
        repo3 = tmp / "t3"; repo3.mkdir()
        (repo3 / "tools.py").write_text(PY_BODY)
        eng.embed.api_calls = 0
        await eng.add(str(repo3))
        cid3 = await _cid_for(eng, f"file://local{repo3}")
        uri3 = f"file://local{repo3}"
        first_calls = eng.embed.api_calls
        # rewrite the destination with substantially different content, then
        # remove the original. We can't use os.rename here (that'd preserve
        # inode); instead write the new file and unlink the old.
        new_body = (
            '"""Different module entirely."""\n'
            "from collections.abc import Iterable\n\n"
            "def quux(items: Iterable[int]) -> int:\n"
            "    return sum(items) * 7\n\n"
            "def fnord(seed: int) -> list[int]:\n"
            "    return [(seed << i) for i in range(8)]\n"
        )
        (repo3 / "helpers.py").write_text(new_body)
        (repo3 / "tools.py").unlink()
        eng.embed.api_calls = 0
        await eng.add(str(repo3))
        objs_t3 = await _objs(eng, cid3)
        tasks_t3 = await _tasks(eng, cid3)
        kinds = [t["change_kind"] for t in tasks_t3]
        check(f"T3 no 'renamed' task — sha1 differs and inodes differ "
              f"(kinds={set(kinds)})",
              "renamed" not in {t["change_kind"] for t in tasks_t3
                                if t["object_uri"] in ("/tools.py", "/helpers.py")
                                or t["old_uri"] in ("/tools.py",)})
        check("T3 old path removed",
              "/tools.py" not in objs_t3)
        check("T3 new path added",
              "/helpers.py" in objs_t3)
        check(f"T3 embedding API was called for the new content "
              f"(delta={eng.embed.api_calls})", eng.embed.api_calls >= 1)
        del first_calls  # noqa: F841

        # =====================================================
        # T4 — sha1-fallback (cp+rm: new inode, identical content)
        # =====================================================
        print("\n--- T4 · sha1-fallback rename (cp+rm preserves content, not inode) ---")
        repo4 = tmp / "t4"; repo4.mkdir()
        src = repo4 / "doc.md"
        src.write_text("# Notes\n\nBGP route reflectors reduce iBGP full-mesh requirements.\n")
        eng.embed.api_calls = 0
        await eng.add(str(repo4))
        cid4 = await _cid_for(eng, f"file://local{repo4}")
        uri4 = f"file://local{repo4}"
        old_chunks_t4 = await asyncio.to_thread(
            eng.milvus.get_chunks_by_object, "default", uri4, uri4 + "/doc.md")
        before_calls_t4 = eng.embed.api_calls
        # cp + rm gives the new file a fresh inode; content sha1 stays the same.
        shutil.copy2(src, repo4 / "doc_copy.md")
        src.unlink()
        eng.embed.api_calls = 0
        await eng.add(str(repo4))
        tasks_t4 = await _tasks(eng, cid4)
        renamed_t4 = [t for t in tasks_t4
                      if t["change_kind"] == "renamed" and t["object_uri"] == "/doc_copy.md"]
        check(f"T4 sha1-fallback yielded 'renamed' "
              f"(/doc.md -> /doc_copy.md): {len(renamed_t4)} task(s)",
              len(renamed_t4) == 1 and renamed_t4[0]["old_uri"] == "/doc.md")
        check(f"T4 vectors reused on sha1-pair "
              f"(api delta={eng.embed.api_calls})", eng.embed.api_calls == 0)
        new_chunks_t4 = await asyncio.to_thread(
            eng.milvus.get_chunks_by_object, "default", uri4, uri4 + "/doc_copy.md")
        check(f"T4 chunk count preserved ({len(old_chunks_t4)} -> {len(new_chunks_t4)})",
              len(new_chunks_t4) == len(old_chunks_t4))
        del before_calls_t4

        # =====================================================
        # T5 — multiple renames in one sync
        # =====================================================
        print("\n--- T5 · multiple parallel renames in one sync ---")
        repo5 = tmp / "t5"; repo5.mkdir()
        # Three semantically distinct bodies so the pairings are unambiguous.
        a_body = ("# alpha\n\nphotosynthesis chlorophyll sunlight membrane "
                  "thylakoid grana stroma carbon-fixation rubisco.\n")
        b_body = ("# beta\n\nkubernetes container orchestration controller scheduler "
                  "kubelet kube-proxy etcd apiserver deployment replicaset.\n")
        c_body = ("# gamma\n\nbgp route reflector autonomous-system ibgp ebgp peer "
                  "session next-hop community attribute.\n")
        (repo5 / "a.md").write_text(a_body)
        (repo5 / "b.md").write_text(b_body)
        (repo5 / "c.md").write_text(c_body)
        eng.embed.api_calls = 0
        await eng.add(str(repo5))
        cid5 = await _cid_for(eng, f"file://local{repo5}")
        uri5 = f"file://local{repo5}"
        a_old = await asyncio.to_thread(
            eng.milvus.get_chunks_by_object, "default", uri5, uri5 + "/a.md")
        b_old = await asyncio.to_thread(
            eng.milvus.get_chunks_by_object, "default", uri5, uri5 + "/b.md")
        c_old = await asyncio.to_thread(
            eng.milvus.get_chunks_by_object, "default", uri5, uri5 + "/c.md")
        os.rename(repo5 / "a.md", repo5 / "a2.md")
        os.rename(repo5 / "b.md", repo5 / "b2.md")
        # c unchanged
        eng.embed.api_calls = 0
        await eng.add(str(repo5))
        tasks_t5 = await _tasks(eng, cid5)
        renamed_t5 = [t for t in tasks_t5 if t["change_kind"] == "renamed"]
        pairs = {(t["old_uri"], t["object_uri"]) for t in renamed_t5}
        check(f"T5 exactly two 'renamed' tasks ({len(renamed_t5)})",
              len(renamed_t5) == 2)
        check("T5 both renamings pair up correctly (a->a2, b->b2)",
              ("/a.md", "/a2.md") in pairs and ("/b.md", "/b2.md") in pairs)
        check(f"T5 zero embedding API calls "
              f"(delta={eng.embed.api_calls})", eng.embed.api_calls == 0)
        objs_t5 = await _objs(eng, cid5)
        check("T5 c.md untouched (no rename task, still indexed)",
              "/c.md" in objs_t5
              and not any(t["object_uri"] == "/c.md" and t["change_kind"] == "renamed"
                          for t in tasks_t5))
        a_new = await asyncio.to_thread(
            eng.milvus.get_chunks_by_object, "default", uri5, uri5 + "/a2.md")
        b_new = await asyncio.to_thread(
            eng.milvus.get_chunks_by_object, "default", uri5, uri5 + "/b2.md")
        c_new = await asyncio.to_thread(
            eng.milvus.get_chunks_by_object, "default", uri5, uri5 + "/c.md")
        check("T5 a's vectors reused under a2.md, b's under b2.md, c's unchanged",
              {tuple(c["dense_vec"]) for c in a_new}
              == {tuple(c["dense_vec"]) for c in a_old}
              and {tuple(c["dense_vec"]) for c in b_new}
              == {tuple(c["dense_vec"]) for c in b_old}
              and {tuple(c["dense_vec"]) for c in c_new}
              == {tuple(c["dense_vec"]) for c in c_old})

        # =====================================================
        # T6 — artifact migration on HTML rename
        # =====================================================
        print("\n--- T6 · artifact migration (HTML converted_md follows rename) ---")
        repo6 = tmp / "t6"; repo6.mkdir()
        (repo6 / "notes.html").write_text(
            "<html><body><h1>Notes</h1><p>Octopus chromatophores enable "
            "rapid skin color shifts via dermal muscle contraction.</p></body></html>")
        eng.converter.api_calls = 0
        eng.embed.api_calls = 0
        await eng.add(str(repo6))
        cid6 = await _cid_for(eng, f"file://local{repo6}")
        uri6 = f"file://local{repo6}"
        first_md_calls = eng.converter.api_calls
        # snapshot artifact_cache + on-disk path BEFORE the rename
        row_pre = await eng.meta.fetchone(
            "SELECT storage_path FROM artifact_cache WHERE namespace_id=? "
            "AND object_uri=? AND artifact_kind='converted_md'",
            (ns, uri6 + "/notes.html"))
        old_storage_path = row_pre["storage_path"] if row_pre else None
        check(f"T6 pre-rename: converted_md artifact present "
              f"(markitdown calls={first_md_calls})",
              first_md_calls == 1 and old_storage_path and os.path.exists(old_storage_path))

        os.rename(repo6 / "notes.html", repo6 / "notes_v2.html")
        eng.converter.api_calls = 0
        eng.embed.api_calls = 0
        await eng.add(str(repo6))
        check(f"T6 zero embedding API calls during rename "
              f"(delta={eng.embed.api_calls})", eng.embed.api_calls == 0)
        check(f"T6 zero markitdown calls during rename "
              f"(delta={eng.converter.api_calls})", eng.converter.api_calls == 0)
        # Bytes-level: confirm move_artifacts actually moved the dir
        from mfs_server.storage.ids import sha1_hex
        old_dir = pathlib.Path(cfg.object_store.root) / "artifacts" / ns / sha1_hex(
            (uri6 + "/notes.html").encode())
        new_dir = pathlib.Path(cfg.object_store.root) / "artifacts" / ns / sha1_hex(
            (uri6 + "/notes_v2.html").encode())
        check("T6 object_store: old artifact dir is gone after move_artifacts",
              not old_dir.exists())
        check("T6 object_store: new artifact dir holds the converted_md bytes",
              (new_dir / "converted_md").exists()
              and (new_dir / "converted_md").stat().st_size > 0)
        # And cat on the new path returns the cached markdown without re-running
        # markitdown — this is the agent-visible win.
        cat_v2 = await eng.cat(uri6 + "/notes_v2.html")
        body = cat_v2 if isinstance(cat_v2, str) else (cat_v2 or {}).get("content", "")
        check("T6 cat(new path) returns the cached markdown",
              "Octopus chromatophores" in body)
        check(f"T6 cat re-run added zero markitdown calls "
              f"(delta={eng.converter.api_calls})", eng.converter.api_calls == 0)
        # Honest finding: artifact_cache TABLE rows are NOT updated by move_artifacts.
        # The bytes physically follow the rename (good), but the indirection row
        # keyed by object_uri stays at the old path. last_accessed bumps + LRU
        # eviction won't see the new uri until a fresh _put_artifact happens.
        row_at_old = await eng.meta.fetchone(
            "SELECT 1 FROM artifact_cache WHERE namespace_id=? AND object_uri=? "
            "AND artifact_kind='converted_md'", (ns, uri6 + "/notes.html"))
        row_at_new = await eng.meta.fetchone(
            "SELECT 1 FROM artifact_cache WHERE namespace_id=? AND object_uri=? "
            "AND artifact_kind='converted_md'", (ns, uri6 + "/notes_v2.html"))
        check("T6 finding: artifact_cache table row STILL at old object_uri "
              "(bytes moved, indirection row stale — known gap in v0.4)",
              row_at_old is not None and row_at_new is None)

        # =====================================================
        # T7 — §6.3 priority follows the NEW path bucket
        # =====================================================
        print("\n--- T7 · §6.3 priority bucket follows new path on rename ---")
        repo7 = tmp / "t7"; (repo7 / "src").mkdir(parents=True)
        (repo7 / "tests").mkdir()
        (repo7 / "src" / "foo.py").write_text(PY_BODY)
        await eng.add(str(repo7))
        cid7 = await _cid_for(eng, f"file://local{repo7}")
        # initial task for /src/foo.py
        t7_initial = [t for t in await _tasks(eng, cid7)
                      if t["object_uri"] == "/src/foo.py"]
        check(f"T7 src/foo.py initial task priority = -220 (got "
              f"{t7_initial[0]['priority'] if t7_initial else None})",
              t7_initial and t7_initial[0]["priority"] == -220)
        os.rename(repo7 / "src" / "foo.py", repo7 / "tests" / "foo.py")
        await eng.add(str(repo7))
        t7_after = [t for t in await _tasks(eng, cid7)
                    if t["object_uri"] == "/tests/foo.py"
                    and t["change_kind"] == "renamed"]
        check(f"T7 renamed task priority follows new path = +80 (got "
              f"{t7_after[0]['priority'] if t7_after else None})",
              t7_after and t7_after[0]["priority"] == 80)

        # =====================================================
        # T8 — chained rename a -> a2 -> a3
        # =====================================================
        print("\n--- T8 · chained rename, cumulative re-embed cost stays at 0 ---")
        repo8 = tmp / "t8"; repo8.mkdir()
        (repo8 / "a.md").write_text(
            "# alpha doc\n\nA self-contained doc about widget calibration.\n")
        eng.embed.api_calls = 0
        await eng.add(str(repo8))
        first_calls_t8 = eng.embed.api_calls
        cid8 = await _cid_for(eng, f"file://local{repo8}")
        uri8 = f"file://local{repo8}"
        check(f"T8 initial embed cost ({first_calls_t8} call(s))",
              first_calls_t8 >= 1)
        os.rename(repo8 / "a.md", repo8 / "a2.md")
        eng.embed.api_calls = 0
        await eng.add(str(repo8))
        d1 = eng.embed.api_calls
        os.rename(repo8 / "a2.md", repo8 / "a3.md")
        eng.embed.api_calls = 0
        await eng.add(str(repo8))
        d2 = eng.embed.api_calls
        check(f"T8 step 1 rename: zero embeds (delta={d1})", d1 == 0)
        check(f"T8 step 2 rename: zero embeds (delta={d2})", d2 == 0)
        objs_t8 = await _objs(eng, cid8)
        check("T8 only the final path /a3.md is indexed",
              "/a3.md" in objs_t8 and "/a.md" not in objs_t8 and "/a2.md" not in objs_t8)
        chunks_t8 = await asyncio.to_thread(
            eng.milvus.get_chunks_by_object, "default", uri8, uri8 + "/a3.md")
        check(f"T8 Milvus still holds the chunks at the final path "
              f"({len(chunks_t8)})", len(chunks_t8) >= 1)

    finally:
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        await eng.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'='*46}\n  rename e2e: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
