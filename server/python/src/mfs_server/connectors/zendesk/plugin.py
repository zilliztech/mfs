"""Zendesk connector — record_collection.
Zendesk REST v2 via httpx (no heavy SDK). Basic auth `<email>/token:<api_token>`.
Cursor pagination: `?page[size]=100&page[after]=<cursor>`, response carries
`meta.has_more` + `meta.after_cursor`. Layout /tickets/records.jsonl +
/tickets/comments.jsonl + /users/records.jsonl + /organizations/records.jsonl.

API shape verified against Zendesk Ticketing API docs. NOT end-to-end tested.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Optional

import httpx

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

# path -> (endpoint, response array key). comments.jsonl is special (per-ticket).
_COLLECTIONS = {
    "/tickets/records.jsonl": ("/api/v2/tickets.json", "tickets"),
    "/users/records.jsonl": ("/api/v2/users.json", "users"),
    "/organizations/records.jsonl": ("/api/v2/organizations.json", "organizations"),
}


class ZendeskPlugin(ConnectorPlugin):
    NAME = "zendesk"
    URI_SCHEME = "zendesk"
    DISPLAY_NAME = "Zendesk"
    PROMPT = "Zendesk tickets/users/organizations as <resource>/records.jsonl."
    CAPABILITIES = Capabilities(
        manual_sync=True,
        watch=False,
        cursor_kind="updated_at",
        full_scan=True,
        delete_detection="full_scan",
        paged_cat=True,
    )

    def _cfg(self, k, d=None):
        return (
            self.config.get(k, d) if isinstance(self.config, dict) else getattr(self.config, k, d)
        )

    def _base(self) -> str:
        sub = self._cfg("subdomain")
        return self._cfg("base_url") or f"https://{sub}.zendesk.com"

    def _auth(self):
        return (f"{self._cfg('email')}/token", self._cfg("api_token") or self.credential)

    async def healthcheck(self) -> HealthStatus:
        try:
            async with httpx.AsyncClient(auth=self._auth(), timeout=30) as c:
                r = await c.get(f"{self._base()}/api/v2/tickets.json?page[size]=1")
                r.raise_for_status()
            return HealthStatus(ok=True)
        except Exception as e:  # noqa: BLE001
            return HealthStatus(ok=False, detail=str(e))

    def _parts(self, path: str) -> list[str]:
        return [p for p in path.strip("/").split("/") if p]

    def preset_for(self, path: str):
        return "zendesk.tickets" if path.endswith("/tickets/records.jsonl") else None

    def object_kind_of(self, path: str) -> ObjectKind:
        if path.endswith("records.jsonl") or path.endswith("comments.jsonl"):
            return "record_collection"
        if path.endswith("schema.json"):
            return "table_schema"
        return "directory"

    async def stat(self, path: str) -> PathStat:
        if path.endswith(".jsonl"):
            return PathStat(
                path=path, type="file", media_type="application/x-ndjson", extra={"lazy": True}
            )
        if path.endswith("schema.json"):
            return PathStat(path=path, type="file", media_type="application/json")
        return PathStat(path=path, type="dir")

    async def list(self, path: str) -> list[Entry]:
        parts = self._parts(path)
        if len(parts) == 0:
            return [Entry("tickets", "dir"), Entry("users", "dir"), Entry("organizations", "dir")]
        if len(parts) == 1 and parts[0] == "tickets":
            return [
                Entry("records.jsonl", "file", "application/x-ndjson", extra={"lazy": True}),
                Entry("comments.jsonl", "file", "application/x-ndjson", extra={"lazy": True}),
            ]
        if len(parts) == 1 and parts[0] in ("users", "organizations"):
            return [Entry("records.jsonl", "file", "application/x-ndjson", extra={"lazy": True})]
        return []

    async def read_records(self, path: str, range: Optional[Range] = None) -> AsyncIterator[dict]:
        if path == "/tickets/comments.jsonl":
            async for rec in self._read_comments():
                yield rec
            return
        entry = _COLLECTIONS.get(path)
        if not entry:
            return
        endpoint, key = entry
        limit = self._cfg("max_read_rows", 100000)
        n, after = 0, None
        async with httpx.AsyncClient(auth=self._auth(), timeout=60) as c:
            url = f"{self._base()}{endpoint}?page[size]=100"
            while url and n < limit:
                params = {"page[after]": after} if after else None
                resp = await c.get(url, params=params)
                resp.raise_for_status()
                body = resp.json()
                for rec in body.get(key, []):
                    yield rec
                    n += 1
                meta = body.get("meta", {})
                if not meta.get("has_more"):
                    break
                after = meta.get("after_cursor")
                url = (body.get("links") or {}).get("next") or url
                if (body.get("links") or {}).get("next"):
                    after = None  # links.next already encodes the cursor
            if n >= limit:
                self.ctx.declare_partial(path)  # hit max_read_rows -> partial recall

    async def _read_comments(self) -> AsyncIterator[dict]:
        """All comments, tagged with ticket_id (Zendesk exposes comments per-ticket
        at /api/v2/tickets/{id}/comments.json)."""
        limit = self._cfg("max_read_rows", 100000)
        n, after = 0, None
        async with httpx.AsyncClient(auth=self._auth(), timeout=60) as c:
            tickets_url = f"{self._base()}/api/v2/tickets.json?page[size]=100"
            while tickets_url and n < limit:
                params = {"page[after]": after} if after else None
                body = (await c.get(tickets_url, params=params)).json()
                for t in body.get("tickets", []):
                    cr = await c.get(f"{self._base()}/api/v2/tickets/{t['id']}/comments.json")
                    for cm in cr.json().get("comments", []):
                        cm["ticket_id"] = t["id"]
                        yield cm
                        n += 1
                meta = body.get("meta", {})
                if not meta.get("has_more"):
                    break
                after = meta.get("after_cursor")

    async def fingerprint(self, path: str) -> Optional[str]:
        # count-based change detection: Zendesk exposes /<resource>/count.json,
        # so a re-sync only re-yields a collection whose size changed instead of always
        # marking it modified.
        ep = _COLLECTIONS.get(path)
        if not ep:
            return None
        resource = ep[1]  # tickets / users / organizations
        try:
            async with httpx.AsyncClient(auth=self._auth(), timeout=30) as c:
                r = await c.get(f"{self._base()}/api/v2/{resource}/count.json")
                r.raise_for_status()
                return f"count:{r.json().get('count', {}).get('value')}"
        except Exception:  # noqa: BLE001 - count endpoint unavailable -> fall back to always-sync
            return None

    async def sync(self, opts: SyncOptions) -> AsyncIterator[ObjectChange]:
        self.ctx.declare_enumeration("full")
        old = await self.state.get("objects") or {}
        seen: dict[str, str] = {}
        for p in list(_COLLECTIONS) + ["/tickets/comments.jsonl"]:
            seen[p] = ""
            yield ObjectChange(p, "modified" if p in old else "added")
        for p in set(old) - set(seen):
            yield ObjectChange(p, "deleted")
        await self.state.set("objects", seen)
