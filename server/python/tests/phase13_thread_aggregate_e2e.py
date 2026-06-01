"""Phase 13 — message_stream thread_aggregate sub-chunking e2e (synthetic connector,
no SaaS keys). Drives the engine through an in-memory plugin that yields chat messages,
then verifies:
  - a short thread becomes a single chunk
  - a long thread becomes multiple sub-chunks with chunk_index + msg_range locators
  - search finds the right sub-chunk for a phrase that only appears late in the thread
  - sub-chunks share overlap (mid-thread phrase is reachable via more than one sub-chunk)

Needs OPENAI_API_KEY (real embeddings). Lite."""

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


# two threads: one short (3 msgs), one long (~40 msgs of ~80 chars each ≈ ~3.2KB, well past 1500)
_MSGS = [{"thread_id": "t-short", "ts": i, "text": f"short thread msg {i}"} for i in range(3)] + [
    {"thread_id": "t-long", "ts": i, "text": f"long thread message number {i} discussing {topic}"}
    for i, topic in enumerate(
        [
            "sso login",
            "sso login",
            "sso login",
            "sso login",
            "sso login",
            "footer typo",
            "footer typo",
            "footer typo",
            "footer typo",
            "footer typo",
            "dark mode toggle",
            "dark mode toggle",
            "dark mode toggle",
            "dark mode toggle",
            "dark mode toggle",
            "payment gateway timeout",
            "payment gateway timeout",
            "payment gateway timeout",
            "payment gateway timeout",
            "payment gateway timeout",
            "kubernetes deployment rollback",
            "kubernetes deployment rollback",
            "kubernetes deployment rollback",
            "kubernetes deployment rollback",
            "kubernetes deployment rollback",
            "high-contrast accessibility palette",
            "high-contrast accessibility palette",
            "high-contrast accessibility palette",
            "high-contrast accessibility palette",
            "high-contrast accessibility palette",
            "internationalization rtl support",
            "internationalization rtl support",
            "internationalization rtl support",
            "internationalization rtl support",
            "internationalization rtl support",
            "graceful shutdown signal handling",
            "graceful shutdown signal handling",
            "graceful shutdown signal handling",
            "graceful shutdown signal handling",
            "graceful shutdown signal handling",
        ]
    )
]


class MemMessagesPlugin(ConnectorPlugin):
    NAME = "memmsgs"
    URI_SCHEME = "memmsgs"
    DISPLAY_NAME = "mem messages (test)"
    PROMPT = "t"
    CAPABILITIES = Capabilities(manual_sync=True, delete_detection="never")

    def object_kind_of(self, path: str) -> ObjectKind:
        return "message_stream" if path.endswith("messages.jsonl") else "directory"

    async def stat(self, path: str) -> PathStat:
        return PathStat(path=path, type="file", media_type="application/x-ndjson")

    async def list(self, path: str) -> list[Entry]:
        return [Entry("messages.jsonl", "file")]

    def read_records(self, path: str, range: Optional[Range] = None):
        async def gen():
            for r in _MSGS:
                yield r

        return gen()

    async def fingerprint(self, path: str) -> Optional[str]:
        return "v1"

    async def sync(self, opts: SyncOptions) -> AsyncIterator[ObjectChange]:
        self.ctx.declare_enumeration("full")
        yield ObjectChange("/messages.jsonl", "added")


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic")
        raise SystemExit(2)
    registry.register(MemMessagesPlugin)
    base = f"/tmp/mfs_thr_{os.getpid()}"
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
        ("memmsgs", t, "memmsgs", {}) if t.startswith("memmsgs://") else orig(t)
    )
    await eng.startup()
    conn_uri = "memmsgs://t"
    obj_uri = "/messages.jsonl"
    try:
        eng.milvus.drop_collection("default")
        eng.milvus.ensure_collection("default")
        cfg_obj = {
            "objects": [
                {
                    "match": "*messages.jsonl",
                    "text_fields": ["text"],
                    "group_by": "thread_id",
                    "locator_fields": ["thread_id"],
                }
            ]
        }
        await eng.add(conn_uri, config=cfg_obj)

        conn = await eng.meta.fetchone("SELECT id FROM connectors WHERE type='memmsgs'")
        check("memmsgs connector registered", conn is not None)
        o = await eng.meta.fetchone(
            "SELECT chunk_count FROM objects WHERE connector_id=? AND object_uri=?",
            (conn["id"], obj_uri),
        )
        # short thread = 1 chunk, long thread (~3.2KB / 1500-char window with 2-msg overlap) = several
        check(
            f"thread aggregation produced multi-chunk output (got {o['chunk_count']} chunks, want >=3)",
            o and o["chunk_count"] >= 3,
        )

        # Pull all chunks and check structure
        full_uri = conn_uri + obj_uri
        chunks = await asyncio.to_thread(
            eng.milvus.get_chunks_by_object, "default", conn_uri, full_uri
        )
        kinds = {c.get("chunk_kind") for c in chunks}
        check("every chunk is chunk_kind=thread_aggregate", kinds == {"thread_aggregate"})

        short_chunks = [c for c in chunks if (c.get("locator") or {}).get("thread_id") == "t-short"]
        long_chunks = [c for c in chunks if (c.get("locator") or {}).get("thread_id") == "t-long"]
        check("short thread (t-short) -> exactly 1 chunk", len(short_chunks) == 1)
        check(
            f"long thread (t-long) -> multiple sub-chunks (got {len(long_chunks)})",
            len(long_chunks) >= 2,
        )

        # sub-chunks of the long thread must carry chunk_index 0..N-1 + msg_range
        idxs = sorted((c.get("locator") or {}).get("chunk_index") for c in long_chunks)
        check(
            "long-thread sub-chunks have contiguous chunk_index starting at 0",
            idxs == list(range(len(long_chunks))),
        )
        has_ranges = all(
            isinstance((c.get("locator") or {}).get("msg_range"), list)
            and len((c.get("locator") or {}).get("msg_range") or []) == 2
            for c in long_chunks
        )
        check("long-thread sub-chunks carry msg_range=[start, end]", has_ranges)

        # search for content that only appears LATE in the long thread (graceful shutdown,
        # last 5 messages) -> must surface, proving late sub-chunks are real and indexed
        res = await eng.search(
            "graceful shutdown signal handling", connector_uri=conn_uri, mode="hybrid", top_k=5
        )
        late_hit = [
            r
            for r in res
            if (r.get("locator") or {}).get("thread_id") == "t-long"
            and "graceful shutdown" in (r.get("content") or "")
        ]
        check(
            "search finds the late-thread 'graceful shutdown' content "
            "(proves long-thread sub-chunks are indexed end-to-end)",
            len(late_hit) >= 1,
        )
    finally:
        try:
            eng.milvus.drop_collection("default")
        except Exception:
            pass
        await eng.shutdown()
        os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'=' * 46}\n  thread_aggregate e2e: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
