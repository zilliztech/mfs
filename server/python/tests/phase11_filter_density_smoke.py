"""Phase 11 — index_filter (restricted AST) + cat density modes (--peek/--skim).
index_filter is unit-tested directly (safe eval, no code execution) AND end-to-end
through a synthetic record_collection connector (only matching rows indexed). Density
is tested through engine.cat on a markdown doc. Needs OPENAI_API_KEY (bash -ic). Lite.
"""
import asyncio
import os
from collections.abc import AsyncIterator
from typing import Optional

from mfs_server.common.filter_ast import FilterError, compile_filter
from mfs_server.config import load_server_config
from mfs_server.connectors import registry
from mfs_server.connectors.base import (
    Capabilities, ConnectorPlugin, Entry, ObjectChange, ObjectKind, PathStat, Range, SyncOptions,
)
from mfs_server.engine.engine import Engine, _density_view

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append(bool(cond)); print(f"  [{OK if cond else FAIL}] {name}"); return cond


_ROWS = [
    {"id": 1, "subject": "SSO login broken", "status": "open", "priority": "high"},
    {"id": 2, "subject": "Typo in footer", "status": "closed", "priority": "low"},
    {"id": 3, "subject": "Payment gateway down", "status": "open", "priority": "urgent"},
    {"id": 4, "subject": "Feature request: dark mode", "status": "open", "priority": "low"},
]


class MemRowsPlugin(ConnectorPlugin):
    NAME = "memrows"; URI_SCHEME = "memrows"; DISPLAY_NAME = "mem rows (test)"; PROMPT = "t"
    CAPABILITIES = Capabilities(manual_sync=True, delete_detection="never")

    def object_kind_of(self, path: str) -> ObjectKind:
        return "record_collection" if path.endswith("rows.jsonl") else "directory"

    async def stat(self, path: str) -> PathStat:
        return PathStat(path=path, type="file", media_type="application/x-ndjson")

    async def list(self, path: str) -> list[Entry]:
        return [Entry("rows.jsonl", "file")]

    def read_records(self, path: str, range: Optional[Range] = None):
        async def gen():
            for r in _ROWS:
                yield r
        return gen()

    async def fingerprint(self, path: str) -> Optional[str]:
        return "v1"

    async def sync(self, opts: SyncOptions) -> AsyncIterator[ObjectChange]:
        self.ctx.declare_enumeration("full")
        yield ObjectChange("/tickets/rows.jsonl", "added")


def _unit_filter():
    # safe evaluation
    p = compile_filter('status == "open" and priority in ["high", "urgent"]')
    check("filter: open+high passes", p({"status": "open", "priority": "high"}))
    check("filter: open+urgent passes", p({"status": "open", "priority": "urgent"}))
    check("filter: closed fails", not p({"status": "closed", "priority": "high"}))
    check("filter: open+low fails", not p({"status": "open", "priority": "low"}))
    # injection / disallowed constructs are rejected (no code execution)
    for bad in ['__import__("os")', 'open("x")', 'status.__class__', '1 + 2']:
        try:
            compile_filter(bad)
            check(f"filter rejects: {bad}", False)
        except FilterError:
            check(f"filter rejects: {bad}", True)


def _unit_density():
    md = "# Title\nintro line\n\n## Section A\ndetails a\n\n## Section B\ndetails b\n"
    peek = _density_view(md, ".md", "peek")
    check("peek = headings only", peek == "# Title\n## Section A\n## Section B")
    skim = _density_view(md, ".md", "skim")
    check("skim adds one-line summaries", "intro line" in skim and "details a" in skim)


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic"); raise SystemExit(2)
    _unit_filter()
    _unit_density()

    registry.register(MemRowsPlugin)
    base = f"/tmp/mfs_fd_{os.getpid()}"; os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"; cfg.milvus.uri = base + "_v.db"; cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"; cfg.transformation_cache.db_path = base + "_t.db"
    eng = Engine(cfg)
    orig = eng._resolve_target
    eng._resolve_target = lambda t: ("memrows", t, "memrows", {}) if t.startswith("memrows://") else orig(t)
    await eng.startup()
    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")
        cfg_obj = {"objects": [{"match": "*rows.jsonl", "text_fields": ["subject"],
                                "locator_fields": ["id"], "chunk_strategy": "per_row",
                                "index_filter": 'status == "open" and priority in ["high", "urgent"]'}]}
        await eng.add("memrows://t", config=cfg_obj)
        conn = await eng.meta.fetchone("SELECT id FROM connectors WHERE type='memrows'")
        o = await eng.meta.fetchone(
            "SELECT chunk_count FROM objects WHERE connector_id=? AND object_uri='/tickets/rows.jsonl'",
            (conn["id"],))
        # only rows 1 (high) and 3 (urgent) match the filter -> 2 chunks
        check("index_filter indexed only 2 of 4 rows", o and o["chunk_count"] == 2)
        res = await eng.search("payment gateway", connector_uri="memrows://t", mode="hybrid", top_k=5)
        locs = {(e.get("locator") or {}).get("id") for e in res}
        check("filtered-in row 3 is searchable", 3 in locs)
        res2 = await eng.search("dark mode feature", connector_uri="memrows://t", mode="hybrid", top_k=5)
        locs2 = {(e.get("locator") or {}).get("id") for e in res2}
        check("filtered-out row 4 is NOT indexed", 4 not in locs2)
    finally:
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        await eng.shutdown(); os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'='*46}\n  index_filter + density: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
