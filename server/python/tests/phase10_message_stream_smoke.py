"""Phase 10 message_stream pipeline e2e — verifies the engine's per_group
thread_aggregate path (new for slack/discord/gmail/feishu) WITHOUT any SaaS key:
a synthetic in-memory connector yields canned messages across 3 threads; the engine
must group them into 3 thread_aggregate chunks, embed, upsert to Milvus Lite, and
make them searchable. Needs OPENAI_API_KEY (run via bash -ic). Milvus Lite.
"""
import asyncio
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
    results.append((name, bool(cond)))
    print(f"  [{OK if cond else FAIL}] {name}")
    return cond


# canned messages: 3 threads (t1 SSO outage, t2 billing, t3 deploy)
_MSGS = [
    {"ts": "1", "thread_ts": "t1", "user": "ann", "text": "SSO login is broken after the migration"},
    {"ts": "2", "thread_ts": "t1", "user": "bob", "text": "confirmed, redirect loops back to the IdP"},
    {"ts": "3", "thread_ts": "t2", "user": "cara", "text": "customer disputes the May invoice amount"},
    {"ts": "4", "thread_ts": "t2", "user": "ann", "text": "issuing a partial refund for billing"},
    {"ts": "5", "thread_ts": "t3", "user": "dan", "text": "production deploy of v0.4 finished cleanly"},
]


class MemMsgPlugin(ConnectorPlugin):
    NAME = "memmsg"
    URI_SCHEME = "memmsg"
    DISPLAY_NAME = "Memory Messages (test)"
    PROMPT = "test"
    CAPABILITIES = Capabilities(manual_sync=True, delete_detection="never")

    def object_kind_of(self, path: str) -> ObjectKind:
        return "message_stream" if path.endswith("messages.jsonl") else "directory"

    async def stat(self, path: str) -> PathStat:
        return PathStat(path=path, type="file", media_type="application/x-ndjson")

    async def list(self, path: str) -> list[Entry]:
        return [Entry("messages.jsonl", "file", "application/x-ndjson")]

    async def read_records(self, path: str, range: Optional[Range] = None) -> AsyncIterator[dict]:
        for m in _MSGS:
            yield m

    async def fingerprint(self, path: str) -> Optional[str]:
        return "v1"

    async def sync(self, opts: SyncOptions) -> AsyncIterator[ObjectChange]:
        self.ctx.declare_enumeration("full")
        yield ObjectChange("/channels/general__C1/messages.jsonl", "added")


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic")
        raise SystemExit(2)
    registry.register(MemMsgPlugin)
    base = f"/tmp/mfs_p10ms_{os.getpid()}"
    os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_meta.db"; cfg.milvus.uri = base + "_milvus.db"; cfg.milvus.token = ""
    cfg.object_store.root = base + "_cache"; cfg.transformation_cache.db_path = base + "_tx.db"
    eng = Engine(cfg)
    await eng.startup()
    # allow the memmsg scheme through target resolution
    orig = eng._resolve_target
    eng._resolve_target = lambda t: ("memmsg", t, "memmsg", {}) if t.startswith("memmsg://") else orig(t)
    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")
        msg_config = {"objects": [{"match": "*messages.jsonl",
                                   "text_fields": ["user", "text"],
                                   "chunk_strategy": "per_group", "group_by": "thread_ts"}]}
        await eng.add("memmsg://test", config=msg_config)

        conn = await eng.meta.fetchone("SELECT id FROM connectors WHERE type='memmsg'")
        check("memmsg connector registered", conn is not None)
        o = await eng.meta.fetchone(
            "SELECT chunk_count, search_status FROM objects WHERE connector_id=? "
            "AND object_uri='/channels/general__C1/messages.jsonl'", (conn["id"],))
        check("5 messages -> 3 thread_aggregate chunks",
              o and o["chunk_count"] == 3 and o["search_status"] == "indexed")

        res = await eng.search("single sign-on authentication failure", connector_uri="memmsg://test",
                               mode="hybrid", top_k=3)
        top = res[0] if res else {}
        check("search SSO -> thread_aggregate chunk_kind",
              top.get("metadata", {}).get("chunk_kind") == "thread_aggregate")
        check("search SSO locates thread t1",
              (top.get("locator") or {}).get("thread_ts") == "t1")
        # aggregate content must contain BOTH messages of the thread (grouped, not per-message)
        check("t1 aggregate fuses both replies",
              "broken" in top.get("content", "") and "redirect loops" in top.get("content", ""))

        res2 = await eng.search("refund customer invoice", connector_uri="memmsg://test", mode="hybrid", top_k=3)
        check("search billing -> thread t2",
              res2 and (res2[0].get("locator") or {}).get("thread_ts") == "t2")
    finally:
        try:
            eng.milvus.drop_collection("default")
        except Exception:
            pass
        await eng.shutdown()
        os.system(f"rm -rf '{base}'*")

    passed = sum(1 for _, c in results if c)
    print(f"\n{'='*48}\n  {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
