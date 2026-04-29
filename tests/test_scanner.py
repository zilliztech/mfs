"""Scanner tests."""

from __future__ import annotations

import time

from mfs.config import Config
from mfs.ingest.scanner import Scanner


def test_scan_finds_indexed_files(sample_project):
    s = Scanner(Config())
    files = s.scan([sample_project])
    names = {f.path.name for f in files}
    # README.md, auth.md are indexed; config.json is not_indexed; main.py is indexed
    assert "README.md" in names
    assert "auth.md" in names
    assert "main.py" in names
    assert "config.json" not in names


def test_classify_file():
    s = Scanner(Config())
    from pathlib import Path
    assert s.classify_file(Path("a.md")) == "indexed"
    assert s.classify_file(Path("a.py")) == "indexed"
    assert s.classify_file(Path("a.json")) == "not_indexed"
    assert s.classify_file(Path("a.png")) == "ignored"
    assert s.classify_file(Path("package-lock.json")) == "ignored"


def test_compute_diff_detects_deletion(sample_project):
    s = Scanner(Config())
    files = s.scan([sample_project])
    indexed = {str(files[0].path): "dummyhash"}
    # Add a fake source that's not on disk
    indexed["/nonexistent/foo.md"] = "ignored"
    diff = s.compute_diff(files, indexed, last_sync_time=None)
    assert "/nonexistent/foo.md" in diff.deleted


def test_compute_diff_detects_addition(sample_project):
    s = Scanner(Config())
    files = s.scan([sample_project])
    diff = s.compute_diff(files, {}, last_sync_time=None)
    assert len(diff.added) == len(files)
    assert not diff.deleted
    assert not diff.modified


def test_compute_diff_mtime_fastpath(sample_project):
    s = Scanner(Config())
    files = s.scan([sample_project])
    # Pretend all files are already indexed with any hash
    indexed = {str(f.path): "fakehash" for f in files}
    # last_sync in the future → mtime fastpath skips hash compare → all unchanged
    future = time.time() + 10000
    diff = s.compute_diff(files, indexed, last_sync_time=future)
    assert not diff.modified
    assert not diff.added
    assert set(diff.unchanged) == {str(f.path) for f in files}


def test_compute_diff_modified(sample_project):
    s = Scanner(Config())
    files = s.scan([sample_project])
    indexed = {str(f.path): "wronghash" for f in files}
    diff = s.compute_diff(files, indexed, last_sync_time=None)
    # Every file's real hash differs from "wronghash" → all modified
    assert len(diff.modified) == len(files)
