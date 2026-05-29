"""Phase 14 — sync-engine edge paths.

Anchors the contracts at the boundaries of the normal `add()` flow:

  · --force-index (full=True) — opts.full propagates to the plugin, every file
    re-enters the task queue, BUT the transformation_cache absorbs the embedding
    cost (api_calls delta = 0); chunk_id is idempotent so re-upserts produce
    the same row count, not duplicates.

  · file deletion end-to-end — os.unlink + re-sync yields ObjectChange(deleted);
    objects row, Milvus chunks, artifact_cache row, object_store bytes, and
    file_state row are ALL cleared. Re-running again is a no-op.

  · process=False / worker mode — add() returns a queued job; objects aren't
    populated until run_worker_once() claims and drains it.

  · sync_already_running — the unique partial index ux_jobs_one_pending blocks
    a second add() on a connector whose previous job is still queued. The
    caller gets a clean ValueError.

  · idempotent re-add — sync with no upstream changes runs 0 tasks and makes
    0 embedding API calls.

  · cancel_job — flips the queued job + its tasks to 'cancelled' in one stroke;
    re-add is then unblocked.

Needs OPENAI_API_KEY (bash -ic) so the embedding step can actually run on the
first sync — subsequent syncs verify it stays at zero."""
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


def _seed_repo(root: pathlib.Path) -> None:
    (root / "src").mkdir(parents=True)
    (root / "src" / "auth.py").write_text(
        '"""Auth module."""\n\n'
        "def verify_saml(payload: str) -> bool:\n"
        "    return payload.startswith('<saml:Assertion')\n")
    (root / "src" / "util.py").write_text(
        '"""Utilities."""\n\n'
        "def normalize(name: str) -> str:\n"
        "    return name.lower().strip()\n")
    (root / "README.md").write_text("# demo\n\nA minimal repo for sync-edge tests.\n")


async def _objs(eng: Engine, cid: str) -> dict[str, dict]:
    rows = await eng.meta.fetchall(
        "SELECT object_uri, chunk_count FROM objects WHERE connector_id=?", (cid,))
    return {r["object_uri"]: r for r in rows}


async def _cid_for(eng: Engine, root_uri: str) -> str | None:
    row = await eng.meta.fetchone(
        "SELECT id FROM connectors WHERE root_uri=?", (root_uri,))
    return row["id"] if row else None


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic"); raise SystemExit(2)

    base = f"/tmp/mfs_sync_{os.getpid()}"; os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"; cfg.milvus.uri = base + "_v.db"; cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"; cfg.transformation_cache.db_path = base + "_t.db"
    cfg.summary.enabled = False
    cfg.chunk.chunk_size = 800
    eng = Engine(cfg)
    await eng.startup()
    ns = eng.ns
    tmp = pathlib.Path(f"{base}_work"); tmp.mkdir()

    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")

        # =====================================================
        # T1 — --force-index (full=True) is absorbed by tx_cache
        # =====================================================
        print("\n--- T1 · --force-index walks every file, tx_cache absorbs embed cost ---")
        repo1 = tmp / "t1"; _seed_repo(repo1)
        eng.embed.api_calls = 0
        await eng.add(str(repo1))
        cid1 = await _cid_for(eng, f"file://local{repo1}")
        uri1 = f"file://local{repo1}"
        first_calls = eng.embed.api_calls
        objs_before = await _objs(eng, cid1)
        chunks_before = 0
        for o in objs_before:
            chunks_before += len(await asyncio.to_thread(
                eng.milvus.get_chunks_by_object, "default", uri1, uri1 + o))
        check(f"T1 initial sync embedded {first_calls} batch call(s), "
              f"{chunks_before} chunks indexed across {len(objs_before)} objects",
              first_calls >= 1 and chunks_before >= 1)

        # --force-index: re-process every file, but tx_cache hits keep api_calls flat
        eng.embed.api_calls = 0
        await eng.add(str(repo1), full=True)
        delta = eng.embed.api_calls
        tasks_after = await eng.meta.fetchall(
            "SELECT object_uri, change_kind FROM object_tasks WHERE connector_id=? "
            "AND change_kind != 'dir_summary'", (cid1,))
        body_kinds = [t["change_kind"] for t in tasks_after if t["object_uri"] != "/"]
        check(f"T1 --force-index re-enqueued every body task ({len(body_kinds)} "
              f"task rows present after force pass)", len(body_kinds) >= len(objs_before))
        check(f"T1 --force-index: 0 embedding API calls (tx_cache absorbs cost) "
              f"(delta={delta})", delta == 0)
        # idempotent chunk_id: total Milvus chunk count must be unchanged
        chunks_after = 0
        for o in objs_before:
            chunks_after += len(await asyncio.to_thread(
                eng.milvus.get_chunks_by_object, "default", uri1, uri1 + o))
        check(f"T1 idempotent chunk_id upsert: Milvus chunk total unchanged "
              f"({chunks_before} -> {chunks_after})", chunks_before == chunks_after)

        # =====================================================
        # T2 — file deletion end-to-end
        # =====================================================
        print("\n--- T2 · file deletion: row / chunks / artifact / file_state all cleared ---")
        repo2 = tmp / "t2"; _seed_repo(repo2)
        # Use an HTML file so we exercise the artifact_cache cleanup path too
        (repo2 / "notes.html").write_text(
            "<html><body><h1>Notes</h1><p>Quantum entanglement preserves "
            "correlation under local measurement.</p></body></html>")
        await eng.add(str(repo2))
        cid2 = await _cid_for(eng, f"file://local{repo2}")
        uri2 = f"file://local{repo2}"
        full_html = uri2 + "/notes.html"
        # snapshot pre-delete: objects + chunks + artifact + file_state
        pre_objects = await _objs(eng, cid2)
        pre_chunks = await asyncio.to_thread(
            eng.milvus.get_chunks_by_object, "default", uri2, full_html)
        pre_artifact_row = await eng.meta.fetchone(
            "SELECT storage_path FROM artifact_cache WHERE namespace_id=? "
            "AND object_uri=? AND artifact_kind='converted_md'", (ns, full_html))
        pre_artifact_path = pre_artifact_row["storage_path"] if pre_artifact_row else None
        pre_fs = await eng.meta.fetchone(
            "SELECT 1 FROM file_state WHERE connector_id=? AND path='/notes.html'",
            (cid2,))
        check("T2 pre-delete: html object indexed, chunks present, artifact present, "
              "file_state row exists",
              "/notes.html" in pre_objects and len(pre_chunks) >= 1
              and pre_artifact_path is not None and os.path.exists(pre_artifact_path)
              and pre_fs is not None)

        os.unlink(repo2 / "notes.html")
        await eng.add(str(repo2))

        post_objects = await _objs(eng, cid2)
        post_chunks = await asyncio.to_thread(
            eng.milvus.get_chunks_by_object, "default", uri2, full_html)
        post_artifact_row = await eng.meta.fetchone(
            "SELECT 1 FROM artifact_cache WHERE namespace_id=? "
            "AND object_uri=? AND artifact_kind='converted_md'", (ns, full_html))
        post_fs = await eng.meta.fetchone(
            "SELECT 1 FROM file_state WHERE connector_id=? AND path='/notes.html'",
            (cid2,))
        check("T2 delete: objects row removed", "/notes.html" not in post_objects)
        check(f"T2 delete: Milvus chunks gone ({len(post_chunks)} left)",
              len(post_chunks) == 0)
        check("T2 delete: artifact_cache row gone", post_artifact_row is None)
        check(f"T2 delete: object_store bytes gone (path was {pre_artifact_path})",
              not os.path.exists(pre_artifact_path))
        check("T2 delete: file_state row gone", post_fs is None)

        # idempotent: re-syncing yields no new tasks for the already-gone file
        tasks_pre_redo = await eng.meta.fetchall(
            "SELECT id FROM object_tasks WHERE connector_id=?", (cid2,))
        await eng.add(str(repo2))
        tasks_post_redo = await eng.meta.fetchall(
            "SELECT id FROM object_tasks WHERE connector_id=?", (cid2,))
        check(f"T2 second re-add after delete: no NEW task rows "
              f"({len(tasks_pre_redo)} -> {len(tasks_post_redo)})",
              len(tasks_post_redo) == len(tasks_pre_redo))

        # =====================================================
        # T3 — process=False + run_worker_once()
        # =====================================================
        print("\n--- T3 · process=False leaves job queued; run_worker_once drains it ---")
        repo3 = tmp / "t3"; _seed_repo(repo3)
        job_id = await eng.add(str(repo3), process=False)
        cid3 = await _cid_for(eng, f"file://local{repo3}")
        job_row = await eng.meta.fetchone(
            "SELECT status FROM connector_jobs WHERE id=?", (job_id,))
        check(f"T3 queued job after process=False (status={job_row['status']!r})",
              job_row["status"] in ("queued", "preparing"))
        # At this point object_tasks should be queued, but objects may also be empty
        # since nothing has indexed them yet
        pending_before = await eng.meta.fetchone(
            "SELECT count(*) AS n FROM object_tasks WHERE connector_id=? "
            "AND status IN ('pending','queued')", (cid3,))
        check(f"T3 tasks pending before worker run ({pending_before['n']})",
              pending_before["n"] >= 1)
        drained = await eng.run_worker_once()
        check(f"T3 run_worker_once drained the queued job ({drained!r})",
              drained == job_id)
        job_row2 = await eng.meta.fetchone(
            "SELECT status FROM connector_jobs WHERE id=?", (job_id,))
        check(f"T3 job status='succeeded' after drain (got {job_row2['status']!r})",
              job_row2["status"] == "succeeded")
        # objects now populated
        objs3 = await _objs(eng, cid3)
        check(f"T3 objects populated after worker run ({len(objs3)} rows)",
              len(objs3) >= 3)
        # idle worker call returns None
        none_drain = await eng.run_worker_once()
        check(f"T3 run_worker_once with empty queue returns None ({none_drain!r})",
              none_drain is None)

        # =====================================================
        # T4 — sync_already_running on overlapping add
        # =====================================================
        print("\n--- T4 · sync_already_running on overlapping add ---")
        repo4 = tmp / "t4"; _seed_repo(repo4)
        # Leave a queued job behind, then try ANOTHER process=False add: the
        # 'preparing' / 'queued' partial index (ux_jobs_one_pending) is what
        # actually blocks overlaps. A process=True add would insert with
        # status='running' under a DIFFERENT partial index and not collide.
        first_job_id = await eng.add(str(repo4), process=False)
        try:
            await eng.add(str(repo4), process=False)   # collides on ux_jobs_one_pending
            check("T4 second add SHOULD have raised sync_already_running", False)
        except ValueError as e:
            check(f"T4 second add raised ValueError 'sync_already_running' "
                  f"({str(e)!r})", "sync_already_running" in str(e))
        # let the worker drain it so we don't leak state across tests
        await eng.run_worker_once()
        post_first = await eng.meta.fetchone(
            "SELECT status FROM connector_jobs WHERE id=?", (first_job_id,))
        check(f"T4 originally queued job eventually drained "
              f"(status={post_first['status']!r})", post_first["status"] == "succeeded")

        # =====================================================
        # T5 — idempotent re-add (no upstream changes => 0 work)
        # =====================================================
        print("\n--- T5 · idempotent re-add: 0 new tasks, 0 new embed calls ---")
        repo5 = tmp / "t5"
        (repo5 / "src").mkdir(parents=True)
        # Use content NOT seen in T1..T4 so the first sync genuinely embeds
        # (the tx_cache is shared across sections, so reusing fixture text
        # would make 'first sync' look like 0 API calls).
        (repo5 / "src" / "unique_t5_a.py").write_text(
            '"""T5 module — bryophyte taxonomy."""\n\n'
            "def classify_moss(species: str) -> str:\n"
            '    """Slot the species into the Bryopsida order."""\n'
            "    return f'bryopsida-{species}'\n")
        (repo5 / "README.md").write_text(
            "# T5 fixture\n\nUnique content for tx_cache isolation: "
            "magnetotactic bacteria navigate using membrane-bound magnetosomes.\n")
        eng.embed.api_calls = 0
        await eng.add(str(repo5))
        cid5 = await _cid_for(eng, f"file://local{repo5}")
        first_calls_t5 = eng.embed.api_calls
        tasks_pre = await eng.meta.fetchall(
            "SELECT id FROM object_tasks WHERE connector_id=? "
            "AND change_kind != 'dir_summary'", (cid5,))
        # second add, unchanged tree
        eng.embed.api_calls = 0
        await eng.add(str(repo5))
        delta_calls = eng.embed.api_calls
        tasks_post = await eng.meta.fetchall(
            "SELECT id FROM object_tasks WHERE connector_id=? "
            "AND change_kind != 'dir_summary'", (cid5,))
        new_tasks = len(tasks_post) - len(tasks_pre)
        check(f"T5 first sync embed cost ({first_calls_t5} call(s))",
              first_calls_t5 >= 1)
        check(f"T5 second sync: 0 new tasks ({new_tasks})", new_tasks == 0)
        check(f"T5 second sync: 0 embed API calls (delta={delta_calls})",
              delta_calls == 0)

        # =====================================================
        # T6 — cancel_job on a queued job
        # =====================================================
        print("\n--- T6 · cancel_job flips queued job + its tasks to cancelled ---")
        repo6 = tmp / "t6"; _seed_repo(repo6)
        job6 = await eng.add(str(repo6), process=False)
        cid6 = await _cid_for(eng, f"file://local{repo6}")
        ok = await eng.cancel_job(job6)
        check(f"T6 cancel_job returned True ({ok!r})", ok is True)
        post = await eng.meta.fetchone(
            "SELECT status FROM connector_jobs WHERE id=?", (job6,))
        check(f"T6 job status='cancelled' (got {post['status']!r})",
              post["status"] == "cancelled")
        # pending/running tasks under this job are all 'cancelled'
        pending_after = await eng.meta.fetchone(
            "SELECT count(*) AS n FROM object_tasks WHERE connector_job_id=? "
            "AND status IN ('pending','running')", (job6,))
        check(f"T6 no pending/running tasks left under the cancelled job "
              f"({pending_after['n']})", pending_after["n"] == 0)
        # second cancel returns False (already terminal)
        again = await eng.cancel_job(job6)
        check(f"T6 cancel_job on already-cancelled job returns False ({again!r})",
              again is False)
        # the connector is unblocked: a fresh add succeeds
        await eng.add(str(repo6))
        objs6 = await _objs(eng, cid6)
        check(f"T6 connector unblocked after cancel: re-add indexed objects "
              f"({len(objs6)} rows)", len(objs6) >= 3)

    finally:
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        await eng.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'='*46}\n  sync edges e2e: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
