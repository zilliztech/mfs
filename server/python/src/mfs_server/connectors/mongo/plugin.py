"""MongoDB connector — document-store; record_collection
object_kind. pymongo 4.13+ native async (AsyncMongoClient; find()/list_collection_names()
are awaitable). Layout /<collection>/{schema.json,documents.jsonl} within one database.
NOT end-to-end tested here (no local mongo); interface follows current pymongo async docs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Optional

from pymongo import AsyncMongoClient

from ..base import (
    Capabilities,
    ConnectorPlugin,
    Entry,
    HealthStatus,
    ObjectChange,
    ObjectKind,
    PathStat,
    Range,
    SyncOptions,
)


class MongoPlugin(ConnectorPlugin):
    NAME = "mongo"
    URI_SCHEME = "mongo"
    DISPLAY_NAME = "MongoDB"
    PROMPT = "MongoDB collections as /<collection>/documents.jsonl + schema.json (one database)."
    CAPABILITIES = Capabilities(
        manual_sync=True,
        watch=False,
        cursor_kind="updatedAt",
        full_scan=True,
        delete_detection="full_scan",
        paged_cat=True,
    )

    def __init__(self, config, credential, *, ctx):
        super().__init__(config, credential, ctx=ctx)
        self._client: Optional[AsyncMongoClient] = None

    def _cfg(self, k, d=None):
        return (
            self.config.get(k, d) if isinstance(self.config, dict) else getattr(self.config, k, d)
        )

    def _db(self):
        return self._client[self._cfg("database")]

    async def connect(self) -> None:
        self._client = AsyncMongoClient(self._cfg("uri") or self.credential)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def healthcheck(self) -> HealthStatus:
        try:
            await self._client.admin.command("ping")
            return HealthStatus(ok=True)
        except Exception as e:  # noqa: BLE001
            return HealthStatus(ok=False, detail=str(e))

    def _parts(self, path: str) -> list[str]:
        return [p for p in path.strip("/").split("/") if p]

    async def _collections(self) -> list[str]:
        return sorted(await self._db().list_collection_names())

    def object_kind_of(self, path: str) -> ObjectKind:
        if path.endswith("documents.jsonl"):
            return "record_collection"
        if path.endswith("schema.json"):
            return "table_schema"
        return "directory"

    async def stat(self, path: str) -> PathStat:
        if path.endswith("documents.jsonl"):
            return PathStat(
                path=path,
                type="file",
                media_type="application/x-ndjson",
                fingerprint=await self.fingerprint(path),
                extra={"lazy": True},
            )
        if path.endswith("schema.json"):
            return PathStat(path=path, type="file", media_type="application/json")
        return PathStat(path=path, type="dir")

    async def list(self, path: str) -> list[Entry]:
        parts = self._parts(path)
        if len(parts) == 0:
            return [Entry(c, "dir") for c in await self._collections()]
        if len(parts) == 1:
            return [
                Entry("schema.json", "file", "application/json"),
                Entry("documents.jsonl", "file", "application/x-ndjson", extra={"lazy": True}),
            ]
        return []

    async def read_records(self, path: str, range: Optional[Range] = None) -> AsyncIterator[dict]:
        parts = self._parts(path)
        if len(parts) == 2 and parts[1] == "documents.jsonl":
            lim = self._cfg("max_read_docs", 100000)
            if range is not None:
                # cat --range pushdown: skip/limit at the source
                off = max(0, int(range.start))
                cnt = max(0, int(range.end) - off)
                cursor = self._db()[parts[0]].find().skip(off).limit(cnt)
            else:
                if await self._db()[parts[0]].estimated_document_count() > lim:
                    self.ctx.declare_partial(path)  # capped -> search_status=partial
                cursor = self._db()[parts[0]].find(limit=lim)
            async for doc in cursor:
                if "_id" in doc:
                    doc["_id"] = str(doc["_id"])
                yield doc
        elif len(parts) == 2 and parts[1] == "schema.json":
            # sample-inferred schema (first doc's keys)
            doc = await self._db()[parts[0]].find_one()
            yield {"collection": parts[0], "fields": sorted((doc or {}).keys())}

    _CURSOR_CANDIDATES = (
        "updatedAt",
        "updated_at",
        "modifiedAt",
        "modified_at",
        "lastModified",
        "mtime",
    )

    async def fingerprint(self, path: str) -> Optional[str]:
        parts = self._parts(path)
        if len(parts) == 2 and parts[1] == "documents.jsonl":
            coll = self._db()[parts[0]]
            cnt = await coll.estimated_document_count()
            # documents have no fixed schema; use a configured/common update-timestamp
            # field so edits that keep the count are still detected (else count only).
            field = self._cfg("cursor_field")
            if not field:
                doc = await coll.find_one()
                field = next((c for c in self._CURSOR_CANDIDATES if doc and c in doc), None)
            mx = None
            if field:
                async for r in await coll.aggregate(
                    [{"$group": {"_id": None, "m": {"$max": f"${field}"}}}]
                ):
                    mx = str(r.get("m"))
                    break
            return f"count:{cnt}|{field}:{mx}" if field else f"count:{cnt}"
        if len(parts) == 2 and parts[1] == "schema.json":
            doc = await self._db()[parts[0]].find_one()
            return "schema:" + ";".join(sorted((doc or {}).keys()))
        return None

    async def sync(self, opts: SyncOptions) -> AsyncIterator[ObjectChange]:
        self.ctx.declare_enumeration("full")
        old = await self.state.get("collections") or {}
        seen: dict[str, str] = {}
        for coll in await self._collections():
            for leaf in ("schema.json", "documents.jsonl"):
                p = f"/{coll}/{leaf}"
                fp = await self.fingerprint(p) or ""
                seen[p] = fp
                if opts.full or old.get(p) != fp:
                    yield ObjectChange(p, "modified" if p in old else "added")
        for p in set(old) - set(seen):
            yield ObjectChange(p, "deleted")
        await self.state.set("collections", seen)
