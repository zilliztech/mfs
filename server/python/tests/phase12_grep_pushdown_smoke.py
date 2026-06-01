"""Phase 12 — grep connector pushdown. A synthetic connector
implements grep() (source-side exact match); engine.grep must use it (via=pushdown)
instead of falling through to BM25/linear. No OpenAI key needed. Milvus Lite.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from typing import Optional

from mfs_server.config import load_server_config
from mfs_server.connectors import registry
from mfs_server.connectors.base import (
    Capabilities,
    ConnectorPlugin,
    Entry,
    GrepMatch,
    GrepOptions,
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


_ROWS = [{"id": 1, "subject": "SSO login broken"}, {"id": 2, "subject": "dark mode"}]


class MemGrepPlugin(ConnectorPlugin):
    NAME = "memgrep"
    URI_SCHEME = "memgrep"
    DISPLAY_NAME = "grep (test)"
    PROMPT = "t"
    CAPABILITIES = Capabilities(manual_sync=True, grep_pushdown=True, delete_detection="never")

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

    async def grep(self, pattern, path, options: GrepOptions):
        # source-side exact match over text_fields -> GrepMatch with locator
        async def gen():
            for r in _ROWS:
                if any(pattern.lower() in str(r.get(f, "")).lower() for f in options.text_fields):
                    yield GrepMatch(
                        path=path, locator={"id": r["id"]}, content=str(r.get("subject", ""))
                    )

        return gen()


async def main():
    registry.register(MemGrepPlugin)
    base = f"/tmp/mfs_gp_{os.getpid()}"
    os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"
    cfg.milvus.uri = base + "_v.db"
    cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"
    cfg.transformation_cache.db_path = base + "_t.db"
    eng = Engine(cfg)
    orig = eng._resolve_target
    eng._resolve_target = lambda t: (
        ("memgrep", t, "memgrep", {}) if t.startswith("memgrep://") else orig(t)
    )
    await eng.startup()
    try:
        eng.milvus.drop_collection("default")
        eng.milvus.ensure_collection("default")
        # register the connector (sync only — no embedding) with text_fields config
        cfg_obj = {
            "objects": [
                {"match": "*rows.jsonl", "text_fields": ["subject"], "locator_fields": ["id"]}
            ]
        }
        await eng.register_or_get_connector("memgrep://t", "memgrep", cfg_obj)

        hits = await eng.grep("SSO", "memgrep://t/t/rows.jsonl")
        check(
            "grep used connector pushdown (via=pushdown)",
            hits and all(h["via"] == "pushdown" for h in hits),
        )
        check("pushdown matched row 1 only", len(hits) == 1 and hits[0]["locator"] == {"id": 1})
        check("pushdown carries content", "SSO" in hits[0]["content"])

        none = await eng.grep("nonexistent-term-xyz", "memgrep://t/t/rows.jsonl")
        check("pushdown no-match returns empty", none == [])
    finally:
        try:
            eng.milvus.drop_collection("default")
        except Exception:
            pass
        await eng.shutdown()
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'=' * 46}\n  grep pushdown: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
