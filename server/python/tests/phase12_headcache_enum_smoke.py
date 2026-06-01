"""Phase 12 — head_cache (structured head fast path) + enumeration-mode delete guard.
Needs OPENAI_API_KEY (bash -ic). Milvus Lite.
  A) incremental connector emitting a 'deleted' change MUST NOT enqueue a delete task.
  B) a full connector's structured object pre-caches first rows -> head reads cache.
"""

import asyncio
import json
import os
from collections.abc import AsyncIterator
from typing import Optional

from mfs_server.config import load_server_config
from mfs_server.connectors import registry
from mfs_server.connectors.base import (
    Capabilities,
    ConnectorPlugin,
    Entry,
    ObjectChange,
    ObjectKind,
    PathStat,
    Range,
    SyncOptions,
)
from mfs_server.engine.engine import Engine

OK, FAIL = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond):
    results.append(bool(cond))
    print(f"  [{OK if cond else FAIL}] {name}")
    return cond


class IncPlugin(ConnectorPlugin):
    """Declares INCREMENTAL enumeration and (wrongly) emits a delete — framework must drop it."""

    NAME = "meminc"
    URI_SCHEME = "meminc"
    DISPLAY_NAME = "inc (test)"
    PROMPT = "t"
    CAPABILITIES = Capabilities(manual_sync=True, delete_detection="state_change")

    def object_kind_of(self, path):
        return "document"

    async def stat(self, path):
        return PathStat(path=path, type="file", media_type="text/markdown")

    async def list(self, path):
        return []

    async def read(self, path, range=None) -> AsyncIterator[bytes]:
        yield b"# Note\nincremental content\n"

    async def fingerprint(self, path):
        return "v1"

    async def sync(self, opts: SyncOptions) -> AsyncIterator[ObjectChange]:
        self.ctx.declare_enumeration("incremental")
        yield ObjectChange("/new.md", "added")
        yield ObjectChange("/old.md", "deleted")  # must be ignored (incremental)


_ROWS = [{"id": i, "subject": f"ticket {i}", "body": f"body text {i}"} for i in range(1, 6)]


class HCPlugin(ConnectorPlugin):
    NAME = "memhc"
    URI_SCHEME = "memhc"
    DISPLAY_NAME = "headcache (test)"
    PROMPT = "t"
    CAPABILITIES = Capabilities(manual_sync=True, delete_detection="never", paged_cat=True)

    def object_kind_of(self, path):
        return "record_collection" if path.endswith("rows.jsonl") else "directory"

    async def stat(self, path):
        return PathStat(path=path, type="file", media_type="application/x-ndjson")

    async def list(self, path):
        return [Entry("rows.jsonl", "file")]

    def read_records(self, path, range=None):
        async def gen():
            for r in _ROWS:
                yield r

        return gen()

    async def fingerprint(self, path):
        return "v1"

    async def sync(self, opts: SyncOptions) -> AsyncIterator[ObjectChange]:
        self.ctx.declare_enumeration("full")
        yield ObjectChange("/t/rows.jsonl", "added")


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic")
        raise SystemExit(2)
    registry.register(IncPlugin)
    registry.register(HCPlugin)
    base = f"/tmp/mfs_he_{os.getpid()}"
    os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"
    cfg.milvus.uri = base + "_v.db"
    cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"
    cfg.transformation_cache.db_path = base + "_t.db"
    eng = Engine(cfg)
    orig = eng._resolve_target

    def _res(t):
        for s in ("meminc", "memhc"):
            if t.startswith(s + "://"):
                return (s, t, s, {})
        return orig(t)

    eng._resolve_target = _res
    await eng.startup()
    try:
        eng.milvus.drop_collection("default")
        eng.milvus.ensure_collection("default")

        # A) enumeration guard
        await eng.add("meminc://t")
        cid = (await eng.meta.fetchone("SELECT id FROM connectors WHERE type='meminc'"))["id"]
        kinds = await eng.meta.fetchall(
            "SELECT change_kind, count(*) AS n FROM object_tasks WHERE connector_id=? GROUP BY change_kind",
            (cid,),
        )
        km = {k["change_kind"]: k["n"] for k in kinds}
        check("incremental: 'added' task enqueued", km.get("added") == 1)
        check("incremental: 'deleted' task DROPPED (enum guard)", "deleted" not in km)

        # B) head_cache
        hc_cfg = {
            "objects": [
                {
                    "match": "*rows.jsonl",
                    "text_fields": ["subject", "body"],
                    "locator_fields": ["id"],
                    "chunk_strategy": "per_row",
                }
            ]
        }
        await eng.add("memhc://t", config=hc_cfg)
        hcid = (await eng.meta.fetchone("SELECT id FROM connectors WHERE type='memhc'"))["id"]
        art = await asyncio.to_thread(
            eng.object_store.get_artifact, eng.ns, "memhc://t/t/rows.jsonl", "head_cache"
        )
        check("head_cache artifact written", art is not None and b"ticket 1" in art)
        h = await eng.head("memhc://t/t/rows.jsonl", n=2)
        rows = [json.loads(x) for x in h.strip().splitlines()]
        check("head -n 2 returns first 2 rows from cache", len(rows) == 2 and rows[0]["id"] == 1)
        _ = hcid
    finally:
        try:
            eng.milvus.drop_collection("default")
        except Exception:
            pass
        await eng.shutdown()
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'=' * 46}\n  head_cache + enum guard: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
