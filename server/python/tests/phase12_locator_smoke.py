"""Phase 12 — cat --locator + structured cat/head pushdown. A synthetic
record_collection connector exposes rows.jsonl; we index it, then reopen a single
record by its locator (the search-result reopen workflow) and read ranges/head over
the lazy structured object. Needs OPENAI_API_KEY (bash -ic). Milvus Lite.
"""
import asyncio
import json
import os
from collections.abc import AsyncIterator
from typing import Optional

from mfs_server.config import load_server_config
from mfs_server.connectors import registry
from mfs_server.connectors.base import (
    Capabilities, ConnectorPlugin, Entry, ObjectChange, ObjectKind, PathStat, Range, SyncOptions,
)
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append(bool(cond)); print(f"  [{OK if cond else FAIL}] {name}"); return cond


_ROWS = [
    {"id": 10, "subject": "SSO login broken", "body": "redirect loops to IdP"},
    {"id": 11, "subject": "Invoice dispute", "body": "customer wants a refund"},
    {"id": 12, "subject": "Dark mode", "body": "please add a dark theme toggle"},
]


class MemCatPlugin(ConnectorPlugin):
    NAME = "memcat"; URI_SCHEME = "memcat"; DISPLAY_NAME = "mem cat (test)"; PROMPT = "t"
    CAPABILITIES = Capabilities(manual_sync=True, delete_detection="never", paged_cat=True)

    def object_kind_of(self, path: str) -> ObjectKind:
        return "record_collection" if path.endswith("rows.jsonl") else "directory"

    async def stat(self, path: str) -> PathStat:
        if path.endswith("rows.jsonl"):
            return PathStat(path=path, type="file", media_type="application/x-ndjson")
        return PathStat(path=path, type="dir")

    async def list(self, path: str) -> list[Entry]:
        if path in ("/", ""):
            return [Entry("tickets", "dir")]
        return [Entry("rows.jsonl", "file", "application/x-ndjson")]

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


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic"); raise SystemExit(2)
    registry.register(MemCatPlugin)
    base = f"/tmp/mfs_loc_{os.getpid()}"; os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"; cfg.milvus.uri = base + "_v.db"; cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"; cfg.transformation_cache.db_path = base + "_t.db"
    eng = Engine(cfg)
    orig = eng._resolve_target
    eng._resolve_target = lambda t: ("memcat", t, "memcat", {}) if t.startswith("memcat://") else orig(t)
    await eng.startup()
    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")
        cfg_obj = {"objects": [{"match": "*rows.jsonl", "text_fields": ["subject", "body"],
                                "locator_fields": ["id"], "chunk_strategy": "per_row"}]}
        await eng.add("memcat://t", config=cfg_obj)

        # search returns a locator
        res = await eng.search("refund invoice dispute", connector_uri="memcat://t", mode="hybrid", top_k=3)
        top = res[0] if res else {}
        loc = top.get("locator")
        check("search hit carries locator {id}", isinstance(loc, dict) and loc.get("id") == 11)

        # reopen that exact record via cat --locator
        src = top["source"]
        out = await eng.cat(src, locator={"id": 11})
        rec = json.loads(out["content"]) if isinstance(out, dict) else json.loads(out)
        check("cat --locator returns the exact record", rec.get("id") == 11 and "refund" in rec["body"])

        # non-existent locator -> locator_not_found
        try:
            await eng.cat(src, locator={"id": 999})
            check("missing locator raises", False)
        except ValueError as e:
            check("missing locator -> locator_not_found", str(e) == "locator_not_found")

        # structured cat range pushdown (records, not bytes)
        out2 = await eng.cat(src, range=(0, 2))
        lines = out2.strip().splitlines()
        check("cat --range 0:2 -> first 2 records", len(lines) == 2 and json.loads(lines[0])["id"] == 10)

        # head over structured
        h = await eng.head(src, n=1)
        check("head -n 1 -> first record", json.loads(h.strip().splitlines()[0])["id"] == 10)

        # ls now works on a structured connector too
        entries = (await eng.ls("memcat://t/tickets"))["entries"]
        check("ls lists rows.jsonl on structured connector",
              any(e["name"] == "rows.jsonl" for e in entries))
    finally:
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        await eng.shutdown(); os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'='*46}\n  cat --locator + structured: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
