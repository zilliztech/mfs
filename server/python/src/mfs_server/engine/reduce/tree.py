"""DirTreeBuilder — per-job in-memory directory tree for the Reduce subsystem (§6.4.1).

Accumulated synchronously as sync() yields ObjectChanges (no extra DB hit — the okind is
passed in by the caller). At sync end finalize() flips sync_done and pushes any already-ready
leaf dirs (empty dirs) into the SummaryQueue; non-empty leaves are pushed later, as their
Map file tasks succeed (the Map→Reduce notification, §6.4.4).
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field
from typing import Optional


def ancestor_dirs(relpath: str) -> list[str]:
    """All ancestor directory uris of an object uri, shallow→deep, incl the root '/'.
    '/a/b/c.txt' -> ['/', '/a', '/a/b']."""
    parts = [p for p in relpath.split("/") if p]
    dirs = ["/"]
    cur = ""
    for seg in parts[:-1]:  # drop the leaf object itself
        cur = f"{cur}/{seg}"
        dirs.append(cur)
    return dirs


def dir_depth(d: str) -> int:
    """Number of path segments: '/' -> 0, '/a' -> 1, '/a/b' -> 2."""
    return len([p for p in d.split("/") if p])


@dataclass
class DirNode:
    parent: Optional[str]
    depth: int
    children_files: list[tuple[str, str]] = field(default_factory=list)  # (object_uri, okind)
    children_dirs: list[str] = field(default_factory=list)  # direct sub-dir uris
    pending: int = 0  # subtree children (files + sub-dirs) not yet done
    summary: Optional[str] = None


class DirTreeBuilder:
    """Per-job dir tree. Keyed on connector-RELATIVE dir uris ('/', '/a', '/a/b'); the
    job's connector_uri is stored so the coordinator can map a Map success hook's full uri
    back to a relative dir, and prefix dir uris when emitting summary chunks."""

    def __init__(self, job_id: str, connector_uri: str, recursive: bool = True):
        self.job_id = job_id
        self.connector_uri = connector_uri
        self.recursive = recursive
        self.tree: dict[str, DirNode] = {}
        self.sync_done = False

    def _node(self, d: str) -> DirNode:
        node = self.tree.get(d)
        if node is None:
            parent = (posixpath.dirname(d) or "/") if d != "/" else None
            node = DirNode(parent=parent, depth=dir_depth(d))
            self.tree[d] = node
        return node

    def add(self, uri: str, okind: str) -> None:
        """Called once per sync() yield (non-deleted change). uri is the connector-relative
        object uri ('/a/b/c.txt'). okind is passed in by the caller — no DB lookup."""
        if not self.recursive:
            # non-recursive: a single root summary folding every object directly
            root = self._node("/")
            root.children_files.append((uri, okind))
            root.pending += 1
            return
        ancestors = ancestor_dirs(uri)
        parent_dir = posixpath.dirname(uri) or "/"
        for d in ancestors:
            node = self._node(d)
            if d == parent_dir:
                node.children_files.append((uri, okind))
                node.pending += 1
            # link the direct sub-dir of d that lies on this object's ancestor chain
            for sub in ancestors:
                if (
                    sub != d
                    and (posixpath.dirname(sub) or "/") == d
                    and sub not in node.children_dirs
                ):
                    node.children_dirs.append(sub)
                    node.pending += 1

    def finalize(self, summary_queue) -> list[str]:
        """sync() ended: flip sync_done and push leaf dirs that are already complete (no
        sub-dirs AND no outstanding files — i.e. empty dirs). Returns the pushed uris.
        Non-empty leaves are pushed later by on_object_task_succeeded as their files land."""
        self.sync_done = True
        pushed = []
        for dir_uri, node in self.tree.items():
            if not node.children_dirs and node.pending == 0:
                summary_queue.push(self.job_id, dir_uri, node.depth)
                pushed.append(dir_uri)
        return pushed
