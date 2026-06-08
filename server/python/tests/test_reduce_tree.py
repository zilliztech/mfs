"""Unit tests for reduce/tree.py — DirTreeBuilder accumulation + finalize."""

from __future__ import annotations

from mfs_server.engine.reduce.tree import DirTreeBuilder, ancestor_dirs, dir_depth


class _FakeQueue:
    def __init__(self):
        self.pushed: list[tuple] = []

    def push(self, job_id, dir_uri, depth):
        self.pushed.append((job_id, dir_uri, depth))


def test_ancestor_dirs_and_depth():
    assert ancestor_dirs("/a/b/c.txt") == ["/", "/a", "/a/b"]
    assert ancestor_dirs("/top.txt") == ["/"]
    assert dir_depth("/") == 0
    assert dir_depth("/a") == 1
    assert dir_depth("/a/b") == 2


def test_accumulation_pending_counts():
    b = DirTreeBuilder("job1", "file:///r")
    b.add("/sub1/f1.md", "document")
    b.add("/sub2/f2.md", "document")
    b.add("/sub1/f3.md", "document")

    root = b.tree["/"]
    # root has two sub-dirs (sub1, sub2), no direct files -> pending == 2
    assert sorted(root.children_dirs) == ["/sub1", "/sub2"]
    assert root.children_files == []
    assert root.pending == 2
    assert root.parent is None and root.depth == 0

    sub1 = b.tree["/sub1"]
    assert [u for u, _ in sub1.children_files] == ["/sub1/f1.md", "/sub1/f3.md"]
    assert sub1.children_dirs == []  # true leaf
    assert sub1.pending == 2  # two files
    assert sub1.parent == "/" and sub1.depth == 1

    sub2 = b.tree["/sub2"]
    assert [u for u, _ in sub2.children_files] == ["/sub2/f2.md"]
    assert sub2.pending == 1


def test_okind_stored_per_file():
    b = DirTreeBuilder("j", "c")
    b.add("/d/pic.png", "image")
    b.add("/d/code.py", "code")
    files = dict(b.tree["/d"].children_files)
    assert files == {"/d/pic.png": "image", "/d/code.py": "code"}


def test_finalize_before_sync_done_pushes_nothing_until_called():
    b = DirTreeBuilder("j", "c")
    b.add("/sub/f.md", "document")
    q = _FakeQueue()
    assert b.sync_done is False
    # leaves with outstanding files are NOT pushed at finalize (they await file successes)
    b.finalize(q)
    assert b.sync_done is True
    assert q.pushed == []  # /sub has pending=1 (its file), root has a sub-dir -> neither ready


def test_finalize_pushes_only_ready_true_leaves():
    b = DirTreeBuilder("j", "c")
    b.add("/withfile/f.md", "document")
    # manufacture an empty leaf dir (no sub-dirs, no files) to prove finalize pushes it
    from mfs_server.engine.reduce.tree import DirNode

    b.tree["/empty"] = DirNode(parent="/", depth=1)
    q = _FakeQueue()
    b.finalize(q)
    # only /empty is a ready true-leaf (no sub-dirs AND pending 0); /withfile still pending its file
    assert q.pushed == [("j", "/empty", 1)]


def test_non_recursive_flattens_to_root():
    b = DirTreeBuilder("j", "c", recursive=False)
    b.add("/a/deep/f1.md", "document")
    b.add("/b/g.md", "document")
    assert set(b.tree) == {"/"}
    root = b.tree["/"]
    assert [u for u, _ in root.children_files] == ["/a/deep/f1.md", "/b/g.md"]
    assert root.children_dirs == [] and root.pending == 2
