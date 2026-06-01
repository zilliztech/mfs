"""Phase 2 file connector smoke — `uv run python tests/phase2_file_connector_smoke.py`.

Drives FilePlugin.sync directly (no engine) through the full change lifecycle:
added / ignore / indexed->skip / modified / renamed(inode) / deleted, and checks
file_state transitions + declare_enumeration. Uses a temp dir + temp metadata DB.
"""

import asyncio
import os
import shutil
import tempfile

from mfs_server.config import load_server_config
from mfs_server.connectors.base import ConnectorContext, SyncOptions
from mfs_server.connectors.file.plugin import FileConfig, FilePlugin
from mfs_server.storage.file_state import FileStateStore
from mfs_server.storage.metadata import MetadataStore

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append((name, cond))
    print(f"  [{OK if cond else FAIL}] {name}")
    return cond


class MemState:
    def __init__(self):
        self.d = {}

    async def get(self, k):
        return self.d.get(k)

    async def set(self, k, v):
        self.d[k] = v

    async def delete(self, k):
        self.d.pop(k, None)

    async def checkpoint(self):
        pass


async def collect(plugin, opts=None):
    return [c async for c in plugin.sync(opts or SyncOptions())]


async def mark_all_indexed(plugin, changes):
    for c in changes:
        if c.kind in ("added", "modified", "renamed"):
            await plugin.on_object_indexed(c.uri)
        elif c.kind == "deleted":
            await plugin.on_object_deleted(c.uri)


async def main():
    root = tempfile.mkdtemp(prefix="mfs_file_test_")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = "/tmp/mfs_test_file_meta.db"
    if os.path.exists(cfg.metadata.path):
        os.remove(cfg.metadata.path)
    meta = MetadataStore(cfg)
    await meta.connect()
    await meta.init_schema()
    await meta.execute(
        "INSERT INTO connectors (id, namespace_id, root_uri, type, status) VALUES (?,?,?,?,?)",
        ("c1", "default", f"file://local{root}", "file", "active"),
    )

    # seed files
    os.makedirs(f"{root}/src")
    open(f"{root}/a.md", "w").write("hello world")
    open(f"{root}/src/b.py", "w").write("def f():\n    return 1\n")
    open(f"{root}/ignore.log", "w").write("noise")
    open(f"{root}/.gitignore", "w").write("*.log\n")

    fstate = FileStateStore(meta, "default", "c1")
    ctx = ConnectorContext(MemState(), "c1", "default")
    plugin = FilePlugin(FileConfig(root=root, client_id="local"), None, ctx=ctx)
    plugin.file_state = fstate
    await plugin.connect()

    # 1. first sync -> all added, .log ignored, declares full
    c1 = await collect(plugin)
    uris = {c.uri: c.kind for c in c1}
    check("first sync: a.md added", uris.get("/a.md") == "added")
    check("first sync: src/b.py added", uris.get("/src/b.py") == "added")
    check("first sync: ignore.log NOT present (.gitignore)", "/ignore.log" not in uris)
    check("declare_enumeration == full", ctx.enumeration_mode == "full")
    fs_a = await fstate.get("/a.md")
    check("a.md file_state staged", fs_a is not None and fs_a["status"] == "staged")
    await mark_all_indexed(plugin, c1)
    fs_a = await fstate.get("/a.md")
    check("a.md -> indexed after callback", fs_a["status"] == "indexed")

    # 2. no-change sync -> empty
    c2 = await collect(plugin)
    check("second sync: no changes", len(c2) == 0)

    # 3. modify a.md
    open(f"{root}/a.md", "w").write("hello world CHANGED")
    c3 = await collect(plugin)
    check("modify -> modified a.md", {c.uri: c.kind for c in c3}.get("/a.md") == "modified")
    await mark_all_indexed(plugin, c3)

    # 4. rename src/b.py -> src/c.py (same inode via os.rename)
    os.rename(f"{root}/src/b.py", f"{root}/src/c.py")
    c4 = await collect(plugin)
    ren = [c for c in c4 if c.kind == "renamed"]
    check(
        "rename detected as renamed",
        len(ren) == 1 and ren[0].uri == "/src/c.py" and ren[0].old_uri == "/src/b.py",
    )
    check(
        "rename: no added/deleted for b.py/c.py",
        not any(c.uri in ("/src/b.py", "/src/c.py") and c.kind in ("added", "deleted") for c in c4),
    )
    await mark_all_indexed(plugin, c4)
    check("old path b.py gone from file_state", await fstate.get("/src/b.py") is None)
    check("new path c.py in file_state", await fstate.get("/src/c.py") is not None)

    # 5. delete a.md
    os.remove(f"{root}/a.md")
    c5 = await collect(plugin)
    check("delete -> deleted a.md", {c.uri: c.kind for c in c5}.get("/a.md") == "deleted")
    await mark_all_indexed(plugin, c5)
    check("a.md removed from file_state", await fstate.get("/a.md") is None)

    await meta.close()
    shutil.rmtree(root, ignore_errors=True)

    passed = sum(1 for _, c in results if c)
    total = len(results)
    print(f"\n{'=' * 40}\nPhase 2 file connector: {passed}/{total} checks passed")
    if passed != total:
        print("FAILED:", [n for n, c in results if not c])
        raise SystemExit(1)
    print("ALL PASS")


if __name__ == "__main__":
    asyncio.run(main())
