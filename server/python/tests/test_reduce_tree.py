"""Unit tests for reduce/tree.py — DirTreeBuilder accumulation + finalize."""

from __future__ import annotations

from mfs_server.engine.job_lane.tree import DirTreeBuilder, ancestor_dirs, dir_depth


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
    assert sub1.pending == 0  # files do NOT gate a dir; only sub-dirs do
    assert sub1.parent == "/" and sub1.depth == 1

    sub2 = b.tree["/sub2"]
    assert [u for u, _ in sub2.children_files] == ["/sub2/f2.md"]
    assert sub2.pending == 0


def test_okind_stored_per_file():
    b = DirTreeBuilder("j", "c")
    b.add("/d/pic.png", "image")
    b.add("/d/code.py", "code")
    files = dict(b.tree["/d"].children_files)
    assert files == {"/d/pic.png": "image", "/d/code.py": "code"}


def test_finalize_flips_sync_done_and_pushes_leaf_dirs():
    b = DirTreeBuilder("j", "c")
    b.add("/sub/f.md", "document")
    q = _FakeQueue()
    assert b.sync_done is False
    # finalize is the trigger: /sub is a leaf (no sub-dirs) so it is ready to fold the moment
    # enumeration completes — it does NOT wait on its file's embedding. Root still has a sub-dir.
    b.finalize(q)
    assert b.sync_done is True
    assert q.pushed == [("j", "/sub", 1)]


def test_finalize_pushes_all_leaf_dirs():
    b = DirTreeBuilder("j", "c")
    b.add("/withfile/f.md", "document")
    # an empty leaf dir (no sub-dirs, no files) is also ready
    from mfs_server.engine.job_lane.tree import DirNode

    b.tree["/empty"] = DirNode(parent="/", depth=1)
    q = _FakeQueue()
    b.finalize(q)
    # every dir with pending == 0 (no un-summarized sub-dirs) is pushed; root has a sub-dir.
    assert q.pushed == [("j", "/withfile", 1), ("j", "/empty", 1)]


def test_non_recursive_flattens_to_root():
    b = DirTreeBuilder("j", "c", recursive=False)
    b.add("/a/deep/f1.md", "document")
    b.add("/b/g.md", "document")
    assert set(b.tree) == {"/"}
    root = b.tree["/"]
    assert [u for u, _ in root.children_files] == ["/a/deep/f1.md", "/b/g.md"]
    assert root.children_dirs == [] and root.pending == 0  # files do not gate
