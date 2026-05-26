"""Phase 11 — summary chunk_kinds (summary / schema_summary / directory_summary).
A synthetic connector yields one document, one table_schema and one directory object;
with summary.enabled='true' the engine must produce the matching summary chunk for
each and make them searchable. Needs OPENAI_API_KEY (bash -ic). Milvus Lite.
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
    results.append(bool(cond)); print(f"  [{OK if cond else FAIL}] {name}"); return cond


_BIG = ("# Incident Runbook\n" + "When the SSO identity provider fails, sessions cannot be "
        "validated and users are locked out. Failover to the backup token service and page "
        "on-call. " * 40)


class MemSumPlugin(ConnectorPlugin):
    NAME = "memsum"; URI_SCHEME = "memsum"; DISPLAY_NAME = "mem summary (test)"; PROMPT = "t"
    CAPABILITIES = Capabilities(manual_sync=True, delete_detection="never")

    def object_kind_of(self, path: str) -> ObjectKind:
        if path.endswith(".md"):
            return "document"
        if path.endswith("schema.json"):
            return "table_schema"
        return "directory"

    async def stat(self, path: str) -> PathStat:
        t = "dir" if self.object_kind_of(path) == "directory" else "file"
        return PathStat(path=path, type=t, media_type="text/markdown" if path.endswith(".md") else None)

    async def list(self, path: str) -> list[Entry]:
        if path == "/projects":
            return [Entry("alpha.md", "file"), Entry("beta.md", "file"), Entry("notes", "dir")]
        return []

    async def read(self, path: str, range: Optional[Range] = None) -> AsyncIterator[bytes]:
        yield _BIG.encode()

    def read_records(self, path: str, range: Optional[Range] = None):
        if not path.endswith("schema.json"):
            return None

        async def gen():
            yield {"table": "tickets", "columns": [
                {"name": "id", "type": "int"}, {"name": "subject", "type": "text"},
                {"name": "priority", "type": "text"}, {"name": "assignee", "type": "text"}]}
        return gen()

    async def fingerprint(self, path: str) -> Optional[str]:
        return "v1"

    async def sync(self, opts: SyncOptions) -> AsyncIterator[ObjectChange]:
        self.ctx.declare_enumeration("full")
        yield ObjectChange("/runbook.md", "added")
        yield ObjectChange("/tickets/schema.json", "added")
        yield ObjectChange("/projects", "added")


async def _kinds_for(eng, q):
    res = await eng.search(q, connector_uri="memsum://t", mode="hybrid", top_k=10)
    return {e.get("metadata", {}).get("chunk_kind") for e in res}


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — run via bash -ic"); raise SystemExit(2)
    registry.register(MemSumPlugin)
    base = f"/tmp/mfs_sum_{os.getpid()}"; os.system(f"rm -rf '{base}'*")
    cfg = load_server_config(apply_env=False)
    cfg.metadata.path = base + "_m.db"; cfg.milvus.uri = base + "_v.db"; cfg.milvus.token = ""
    cfg.object_store.root = base + "_c"; cfg.transformation_cache.db_path = base + "_t.db"
    cfg.summary.enabled = "true"          # force summaries regardless of size
    eng = Engine(cfg)
    orig = eng._resolve_target
    eng._resolve_target = lambda t: ("memsum", t, "memsum", {}) if t.startswith("memsum://") else orig(t)
    await eng.startup()
    try:
        eng.milvus.drop_collection("default"); eng.milvus.ensure_collection("default")
        await eng.add("memsum://t")

        doc_kinds = await _kinds_for(eng, "what to do when single sign-on provider is down")
        check("document yields body + summary chunks", "body" in doc_kinds and "summary" in doc_kinds)
        sch_kinds = await _kinds_for(eng, "support ticket table columns subject priority")
        check("table_schema yields schema_summary", "schema_summary" in sch_kinds)
        dir_kinds = await _kinds_for(eng, "folder of project markdown files")
        check("directory yields directory_summary", "directory_summary" in dir_kinds)

        # summary cache: re-index hits cache (no new summary API calls)
        calls = eng.summary.api_calls
        await eng.add("memsum://t", full=True)
        check("re-index hits summary cache (0 new summary calls)", eng.summary.api_calls == calls)
    finally:
        try: eng.milvus.drop_collection("default")
        except Exception: pass
        await eng.shutdown(); os.system(f"rm -rf '{base}'*")

    passed = sum(results)
    print(f"\n{'='*46}\n  summary chunk_kinds: {passed}/{len(results)} checks passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
