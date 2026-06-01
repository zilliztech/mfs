"""HubSpot connector — record_collection.
hubspot-api-client (sync; wrapped in asyncio.to_thread). Layout /<object>/records.jsonl
+ schema.json for each CRM object (contacts / companies / deals / tickets / ...).

API verified against hubspot-api-python docs: hubspot.Client.create(access_token=...);
client.crm.<object>.basic_api.get_page(limit=100, after=...) -> result with
.results (each .to_dict()) and .paging.next.after. Sync only. NOT end-to-end tested.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Optional

import hubspot

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

_DEFAULT_OBJECTS = ["contacts", "companies", "deals", "tickets"]


class HubSpotPlugin(ConnectorPlugin):
    NAME = "hubspot"
    URI_SCHEME = "hubspot"
    DISPLAY_NAME = "HubSpot"
    PROMPT = "HubSpot CRM objects as /<object>/records.jsonl (contacts/companies/deals/tickets)."
    CAPABILITIES = Capabilities(
        manual_sync=True,
        watch=False,
        cursor_kind="hs_lastmodifieddate",
        full_scan=True,
        delete_detection="full_scan",
        paged_cat=True,
    )

    def __init__(self, config, credential, *, ctx):
        super().__init__(config, credential, ctx=ctx)
        self._client = None
        # Connect-time result of probing _DEFAULT_OBJECTS for availability
        # in this portal. None until connect() has run with no user-supplied
        # enumerate list; set to the filtered list otherwise.
        self._probed_defaults: Optional[list[str]] = None

    def _cfg(self, k, d=None):
        return (
            self.config.get(k, d) if isinstance(self.config, dict) else getattr(self.config, k, d)
        )

    async def connect(self) -> None:
        token = self._cfg("access_token") or self.credential
        self._client = await asyncio.to_thread(hubspot.Client.create, access_token=token)
        # Probe-and-skip path: if the user hasn't supplied an enumerate list,
        # try get_page(limit=1) on each default object and quietly drop the
        # ones the portal rejects (403 on tickets in Free CRM, 403 on
        # quotes when Sales Hub is off, etc.). When the user IS explicit
        # we respect their list verbatim — a 403 there is a misconfig that
        # should bubble up as the actual sync error.
        if self._cfg("object_types") or self._cfg("objects"):
            return
        available: list[str] = []
        for obj in _DEFAULT_OBJECTS:
            try:
                await asyncio.to_thread(lambda o=obj: self._basic_api(o).get_page(limit=1))
                available.append(obj)
            except Exception:  # noqa: BLE001 — 403/404/etc all skip
                pass
        self._probed_defaults = available

    def _objects(self) -> list[str]:
        # `object_types` is the plugin-level enumerate list (which CRM
        # objects to walk). The framework already reserves `objects` for
        # [[objects]] match configs (text_fields/locator_fields/...);
        # accepting both here would collide on the same key. Prefer
        # `object_types`; fall back to `objects` ONLY when it's the legacy
        # flat list of strings. With neither, use the connect-time probe
        # result (defaults filtered against actual portal availability).
        types = self._cfg("object_types")
        if types:
            return list(types)
        legacy = self._cfg("objects")
        if isinstance(legacy, list) and legacy and all(isinstance(o, str) for o in legacy):
            return list(legacy)
        if self._probed_defaults is not None:
            return list(self._probed_defaults)
        return list(_DEFAULT_OBJECTS)

    def _basic_api(self, obj: str):
        # client.crm.contacts.basic_api / .companies / .deals / .tickets
        return getattr(getattr(self._client.crm, obj), "basic_api")

    async def healthcheck(self) -> HealthStatus:
        try:
            await asyncio.to_thread(lambda: self._basic_api("contacts").get_page(limit=1))
            return HealthStatus(ok=True)
        except Exception as e:  # noqa: BLE001
            return HealthStatus(ok=False, detail=str(e))

    def _parts(self, path: str) -> list[str]:
        return [p for p in path.strip("/").split("/") if p]

    def object_kind_of(self, path: str) -> ObjectKind:
        if path.endswith("records.jsonl"):
            return "record_collection"
        return "directory"

    async def stat(self, path: str) -> PathStat:
        if path.endswith("records.jsonl"):
            return PathStat(
                path=path, type="file", media_type="application/x-ndjson", extra={"lazy": True}
            )
        return PathStat(path=path, type="dir")

    async def list(self, path: str) -> list[Entry]:
        parts = self._parts(path)
        if len(parts) == 0:
            return [Entry(o, "dir") for o in self._objects()]
        if len(parts) == 1:
            return [Entry("records.jsonl", "file", "application/x-ndjson", extra={"lazy": True})]
        return []

    async def read_records(self, path: str, range: Optional[Range] = None) -> AsyncIterator[dict]:
        parts = self._parts(path)
        if len(parts) == 2 and parts[1] == "records.jsonl":
            obj = parts[0]
            api = self._basic_api(obj)
            limit = self._cfg("max_read_rows", 100000)
            n, after = 0, None
            while n < limit:
                page = await asyncio.to_thread(lambda a=after: api.get_page(limit=100, after=a))
                for r in page.results:
                    rec = r.to_dict()
                    # flatten the {properties: {...}} envelope to top-level fields
                    props = rec.pop("properties", None)
                    if isinstance(props, dict):
                        rec.update(props)
                    yield rec
                    n += 1
                nxt = getattr(page, "paging", None)
                after = nxt.next.after if (nxt and nxt.next) else None
                if not after:
                    break

    async def fingerprint(self, path: str) -> Optional[str]:
        return None

    async def sync(self, opts: SyncOptions) -> AsyncIterator[ObjectChange]:
        self.ctx.declare_enumeration("full")
        old = await self.state.get("objects") or {}
        seen: dict[str, str] = {}
        for o in self._objects():
            p = f"/{o}/records.jsonl"
            seen[p] = ""
            yield ObjectChange(p, "modified" if p in old else "added")
        for p in set(old) - set(seen):
            yield ObjectChange(p, "deleted")
        await self.state.set("objects", seen)
