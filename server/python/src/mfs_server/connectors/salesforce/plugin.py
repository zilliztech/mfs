"""Salesforce connector — record_collection.
simple-salesforce (sync; wrapped in asyncio.to_thread). Layout /<object>/{schema.json,
records.jsonl} for each configured SObject (Account / Opportunity / Case / ...).

API verified against simple-salesforce docs: Salesforce(username, password,
security_token) | (instance_url, session_id); sf.query_all_iter(soql) lazy iterator
of record dicts; sf.<Object>.describe() -> {'fields': [{'name','type',...}]}. No async
API (sync only). NOT end-to-end tested (needs org creds).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Optional

from simple_salesforce import Salesforce

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
    safe_ident,
)

# default SObjects to expose if none configured
_DEFAULT_OBJECTS = ["Account", "Contact", "Opportunity", "Case", "Lead"]


class SalesforcePlugin(ConnectorPlugin):
    NAME = "salesforce"
    URI_SCHEME = "salesforce"
    DISPLAY_NAME = "Salesforce"
    PROMPT = "Salesforce SObjects as /<object>/records.jsonl + schema.json."
    CAPABILITIES = Capabilities(
        manual_sync=True,
        watch=False,
        cursor_kind="LastModifiedDate",
        full_scan=True,
        delete_detection="full_scan",
        paged_cat=True,
    )

    def __init__(self, config, credential, *, ctx):
        super().__init__(config, credential, ctx=ctx)
        self._sf: Optional[Salesforce] = None

    def _cfg(self, k, d=None):
        return (
            self.config.get(k, d) if isinstance(self.config, dict) else getattr(self.config, k, d)
        )

    async def connect(self) -> None:
        def build():
            if self._cfg("session_id"):
                return Salesforce(
                    instance_url=self._cfg("instance_url"), session_id=self._cfg("session_id")
                )
            return Salesforce(
                username=self._cfg("username"),
                password=self._cfg("password"),
                security_token=self._cfg("security_token") or self.credential,
                domain=self._cfg("domain", "login"),
            )

        self._sf = await asyncio.to_thread(build)

    async def healthcheck(self) -> HealthStatus:
        try:
            await asyncio.to_thread(self._sf.query, "SELECT Id FROM Organization LIMIT 1")
            return HealthStatus(ok=True)
        except Exception as e:  # noqa: BLE001
            return HealthStatus(ok=False, detail=str(e))

    def _objects(self) -> list[str]:
        return list(self._cfg("objects") or _DEFAULT_OBJECTS)

    def _parts(self, path: str) -> list[str]:
        return [p for p in path.strip("/").split("/") if p]

    def object_kind_of(self, path: str) -> ObjectKind:
        if path.endswith("records.jsonl"):
            return "record_collection"
        if path.endswith("schema.json"):
            return "table_schema"
        return "directory"

    async def stat(self, path: str) -> PathStat:
        if path.endswith("records.jsonl"):
            return PathStat(
                path=path, type="file", media_type="application/x-ndjson", extra={"lazy": True}
            )
        if path.endswith("schema.json"):
            return PathStat(path=path, type="file", media_type="application/json")
        return PathStat(path=path, type="dir")

    async def list(self, path: str) -> list[Entry]:
        parts = self._parts(path)
        if len(parts) == 0:
            return [Entry(o, "dir") for o in self._objects()]
        if len(parts) == 1:
            return [
                Entry("schema.json", "file", "application/json"),
                Entry("records.jsonl", "file", "application/x-ndjson", extra={"lazy": True}),
            ]
        return []

    async def _fields(self, sobject: str) -> list[dict]:
        desc = await asyncio.to_thread(lambda: getattr(self._sf, sobject).describe())
        return desc.get("fields", [])

    async def read_records(self, path: str, range: Optional[Range] = None) -> AsyncIterator[dict]:
        parts = self._parts(path)
        if len(parts) == 2 and parts[1] == "records.jsonl":
            sobject = safe_ident(parts[0])
            fields = [f["name"] for f in await self._fields(sobject)]
            soql = f"SELECT {', '.join(fields)} FROM {sobject}"
            # query_all_iter is a (sync) generator; drain it off-thread
            recs = await asyncio.to_thread(lambda: list(self._sf.query_all_iter(soql)))
            for r in recs:
                r.pop("attributes", None)
                yield r
        elif len(parts) == 2 and parts[1] == "schema.json":
            fields = await self._fields(safe_ident(parts[0]))
            yield {
                "object": parts[0],
                "fields": [
                    {"name": f["name"], "type": f["type"], "label": f.get("label")} for f in fields
                ],
            }

    async def fingerprint(self, path: str) -> Optional[str]:
        parts = self._parts(path)
        if len(parts) == 2 and parts[1] == "records.jsonl":
            obj = safe_ident(parts[0])
            res = await asyncio.to_thread(self._sf.query, f"SELECT COUNT() FROM {obj}")
            total = res.get("totalSize", 0)
            # SystemModstamp is a standard audit field on (virtually) every SObject;
            # max() catches in-place record edits that leave the count unchanged.
            mx = None
            try:
                m = await asyncio.to_thread(
                    self._sf.query, f"SELECT MAX(SystemModstamp) m FROM {obj}"
                )
                recs = m.get("records") or []
                mx = recs[0].get("m") if recs else None
            except Exception:  # noqa: BLE001 - object without SystemModstamp -> count only
                mx = None
            return f"total:{total}|modstamp:{mx}" if mx is not None else f"total:{total}"
        return None

    async def sync(self, opts: SyncOptions) -> AsyncIterator[ObjectChange]:
        self.ctx.declare_enumeration("full")
        old = await self.state.get("objects") or {}
        seen: dict[str, str] = {}
        for o in self._objects():
            p = f"/{o}/records.jsonl"
            fp = await self.fingerprint(p) or ""
            seen[p] = fp
            if opts.full or old.get(p) != fp:
                yield ObjectChange(p, "modified" if p in old else "added")
        for p in set(old) - set(seen):
            yield ObjectChange(p, "deleted")
        await self.state.set("objects", seen)
