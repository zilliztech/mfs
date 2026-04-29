"""Regression tests for the 4 critical/high code-review fixes.

1. ``_escape()`` now escapes backslashes and has a companion
   ``_escape_for_like()`` that also escapes ``%`` / ``_`` wildcards.
2. All ``limit=16384`` hard-caps replaced with ``_query_all`` pagination.
3. ``update_status`` wraps its read-modify-write cycle in a filelock.
4. ``Worker.spawn`` no longer leaks the log file descriptor.

Every test doubles as documentation: read the docstring to see what
the fix actually guards against.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from mfs.config import MilvusConfig
from mfs.store import ChunkRecord, MilvusStore, _escape, _has_like_wildcards


# ------------------------------------------------------------------ helpers

def _record(
    chunk_id: str,
    source: str,
    *,
    text: str = "hello",
    chunk_index: int = 0,
    dim: int = 8,
) -> ChunkRecord:
    return ChunkRecord(
        id=chunk_id,
        source=source,
        parent_dir=str(Path(source).parent),
        chunk_index=chunk_index,
        start_line=1,
        end_line=3,
        chunk_text=text,
        dense_vector=[0.1] * dim,
        content_type="markdown",
        file_hash="h",
        is_dir=False,
        embed_status="complete",
        metadata={},
        account_id="default",
    )


@pytest.fixture
def store(tmp_path):
    cfg = MilvusConfig(uri=str(tmp_path / "milvus.db"))
    s = MilvusStore(cfg, dimension=8)
    s.connect()
    return s


# -------------------------------------------------------- Issue 1: _escape

def test_escape_handles_backslash_and_quote():
    # Backslashes must be escaped first so the subsequent `"` replacement
    # doesn't double-escape the slashes it produces.
    assert _escape("a\\b") == "a\\\\b"
    assert _escape('a"b') == 'a\\"b'
    assert _escape('a\\"b') == 'a\\\\\\"b'


def test_has_like_wildcards_detects_percent_and_underscore():
    # Milvus LIKE has no escape syntax for `%` / `_`, so callers must branch
    # on this helper and do a Python-side post-filter when it returns True.
    assert _has_like_wildcards("a%b")
    assert _has_like_wildcards("a_b")
    assert not _has_like_wildcards("/plain/path/")
    assert not _has_like_wildcards("")
    # _escape must leave LIKE metacharacters alone — they're only significant
    # in the LIKE clause, and equality filters need the literal bytes.
    assert _escape("a%b") == "a%b"
    assert _escape("a_b") == "a_b"


def test_delete_by_prefix_respects_wildcard_escape(store):
    """Without _escape_for_like, ``delete_by_prefix("/foo_")`` would delete
    ``/fooXbar.md`` too because ``_`` is a single-character wildcard."""
    store.insert_chunks([
        _record("id1", "/foo_/a.md"),
        _record("id2", "/fooX/b.md"),
    ])
    store.delete_by_prefix("/foo_/")
    remaining = store.get_indexed_files("/")
    assert "/foo_/a.md" not in remaining
    assert "/fooX/b.md" in remaining


def test_delete_by_prefix_with_percent_sign(store):
    """A literal ``%`` in the prefix used to be treated as match-anything."""
    store.insert_chunks([
        _record("id1", "/tmp/100%-test/a.md"),
        _record("id2", "/tmp/other/b.md"),
    ])
    store.delete_by_prefix("/tmp/100%-test/")
    remaining = store.get_indexed_files("/tmp")
    assert "/tmp/100%-test/a.md" not in remaining
    assert "/tmp/other/b.md" in remaining


def test_delete_by_source_with_quote(store):
    store.insert_chunks([
        _record("id1", '/tmp/quote"name.md'),
        _record("id2", "/tmp/normal.md"),
    ])
    removed = store.delete_by_source('/tmp/quote"name.md')
    assert removed >= 1
    remaining = store.get_indexed_files("/tmp")
    assert '/tmp/quote"name.md' not in remaining
    assert "/tmp/normal.md" in remaining


# ------------------------------------------------------ Issue 2: pagination

def test_query_all_paginates_past_16k(store):
    """Regression for the hard-coded ``limit=16384`` bug: a corpus larger
    than 16k chunks used to silently drop rows from every reader (including
    ``get_indexed_files``, ``count_all``)."""
    count = 16500
    # Insert in batches so the single upsert call doesn't get too large.
    batch: list[ChunkRecord] = []
    for i in range(count):
        # Unique source per row so get_indexed_files returns one entry each.
        batch.append(_record(f"id{i:06d}", f"/bulk/file_{i:06d}.md"))
        if len(batch) >= 500:
            store.insert_chunks(batch)
            batch = []
    if batch:
        store.insert_chunks(batch)

    indexed = store.get_indexed_files("/bulk")
    assert len(indexed) == count, (
        f"get_indexed_files should paginate past 16384; got {len(indexed)}"
    )
    counts = store.count_all()
    assert counts["files"] == count
    assert counts["total_chunks"] == count


# --------------------------------------------------- Issue 3: update_status

def test_update_status_concurrent_safe(mfs_home):
    """Four threads each writing distinct keys must not lose updates.

    The filelock wrapper serializes the RMW cycle inside ``update_status``
    itself. Each thread writes to its own key ``key_<tid>`` — if the
    load/modify/save were not locked, the non-locking baseline would have
    some writer read an older ``status`` (missing other threads' keys) and
    then save it back, stomping their writes. With locking, every key
    written should still be present at the end.
    """
    import mfs.ingest.worker as worker_mod

    iterations_per_thread = 50
    n_threads = 4

    def _worker(tid: int):
        key = f"key_{tid}"
        for i in range(iterations_per_thread):
            worker_mod.update_status(mfs_home, **{key: i})

    threads = [
        threading.Thread(target=_worker, args=(tid,)) for tid in range(n_threads)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    final = worker_mod.load_status(mfs_home)
    for tid in range(n_threads):
        key = f"key_{tid}"
        assert key in final, (
            f"Key {key!r} got stomped: keys present = {sorted(final)}"
        )
        assert final[key] == iterations_per_thread - 1, (
            f"Final value for {key} = {final[key]}, expected "
            f"{iterations_per_thread - 1}"
        )


def test_update_status_preserves_unrelated_keys(mfs_home):
    """``update_status(k=v)`` must not stomp keys it wasn't asked to change."""
    import mfs.ingest.worker as worker_mod

    worker_mod.update_status(mfs_home, processed=7, total=10, state="indexing")
    worker_mod.update_status(mfs_home, processed=8)
    final = worker_mod.load_status(mfs_home)
    assert final["processed"] == 8
    assert final["total"] == 10
    assert final["state"] == "indexing"


# ------------------------------------------------------- Issue 4: spawn FD

def test_spawn_does_not_leak_log_fd(mfs_home, monkeypatch):
    """Every ``Worker.spawn`` must close its end of the log file — otherwise
    ``mfs watch`` leaks one FD per restart."""
    import importlib

    import mfs.ingest.worker as worker_mod
    importlib.reload(worker_mod)
    from mfs.config import Config

    # Count FDs pointing at the log file in the parent process. Using
    # /proc/self/fd is portable enough on Linux (the only supported platform
    # in CLAUDE.md), and avoids pulling in psutil.
    log_path = mfs_home / "worker.log"

    def _fds_to_log() -> int:
        n = 0
        fd_dir = Path("/proc/self/fd")
        if not fd_dir.exists():
            pytest.skip("/proc/self/fd not available on this platform")
        for entry in fd_dir.iterdir():
            try:
                target = os.readlink(entry)
            except OSError:
                continue
            if target == str(log_path):
                n += 1
        return n

    class _FakeProc:
        pid = 4242

    def _fake_popen(args, **kwargs):
        # Mimic subprocess behaviour: the parent's FD for stdout was dup'd
        # into the child; the parent-side ``with open(...)`` is responsible
        # for closing its handle after this returns.
        return _FakeProc()

    config = Config()
    w = worker_mod.Worker(config)
    before = _fds_to_log()
    with patch.object(worker_mod.subprocess, "Popen", side_effect=_fake_popen):
        # Spawn many times so any per-call leak would accumulate.
        for _ in range(20):
            w.spawn()
    after = _fds_to_log()
    # Parent must not accumulate FDs. Allow a slack of 1 in case pytest's
    # own FD capture happens to land on the log path.
    assert after - before <= 1, (
        f"FD leak: {before} before spawn, {after} after 20 spawns"
    )


def test_spawn_writes_pid_atomically(mfs_home, monkeypatch):
    """PID file write must go through a tmp+replace so readers never see a
    partially-written file."""
    import importlib

    import mfs.ingest.worker as worker_mod
    importlib.reload(worker_mod)
    from mfs.config import Config

    class _FakeProc:
        pid = 98765

    def _fake_popen(args, **kwargs):
        return _FakeProc()

    config = Config()
    w = worker_mod.Worker(config)
    with patch.object(worker_mod.subprocess, "Popen", side_effect=_fake_popen):
        pid = w.spawn()
    assert pid == 98765
    pid_file = mfs_home / "worker.pid"
    assert pid_file.read_text().strip() == "98765"
    # tmp file must have been cleaned up by os.replace
    assert not (mfs_home / "worker.pid.tmp").exists()
