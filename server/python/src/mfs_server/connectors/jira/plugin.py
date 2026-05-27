"""Jira connector — record_collection.
atlassian-python-api Jira (sync; wrapped in asyncio.to_thread). Layout
/projects/<proj>/issues.jsonl + /users.jsonl. Each issue is one record;
framework's record_collection pipeline does per_row chunk (text_fields like
summary, description) + locator (key).

API verified against atlassian-python-api docs (Jira(url, username, password|token),
jira.projects(), jira.jql(jql, start, limit) -> {'issues': [...], 'total': N}).
NOT end-to-end tested (needs a Jira site + token).
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Optional

from atlassian import Jira

from ..base import (
    Capabilities, ConnectorPlugin, Entry, HealthStatus, ObjectChange, ObjectKind,
    PathStat, Range, SyncOptions,
)


class JiraPlugin(ConnectorPlugin):
    NAME = "jira"
    URI_SCHEME = "jira"
    DISPLAY_NAME = "Jira"
    PROMPT = "Jira issues as /projects/<proj>/issues.jsonl + users.jsonl."
    CAPABILITIES = Capabilities(manual_sync=True, watch=False, cursor_kind="updated",
                                full_scan=True, delete_detection="full_scan", paged_cat=True)

    def __init__(self, config, credential, *, ctx):
        super().__init__(config, credential, ctx=ctx)
        self._jira: Optional[Jira] = None

    def _cfg(self, k, d=None):
        return self.config.get(k, d) if isinstance(self.config, dict) else getattr(self.config, k, d)

    async def connect(self) -> None:
        # cloud: username=email + password=API token; server: token=PAT
        def build():
            tok = self._cfg("token")
            if tok:
                return Jira(url=self._cfg("url"), token=tok, cloud=self._cfg("cloud", True))
            return Jira(url=self._cfg("url"), username=self._cfg("username"),
                        password=self._cfg("api_token") or self.credential, cloud=self._cfg("cloud", True))
        self._jira = await asyncio.to_thread(build)

    async def healthcheck(self) -> HealthStatus:
        try:
            await asyncio.to_thread(self._jira.myself)
            return HealthStatus(ok=True)
        except Exception as e:  # noqa: BLE001
            return HealthStatus(ok=False, detail=str(e))

    def _parts(self, path: str) -> list[str]:
        return [p for p in path.strip("/").split("/") if p]

    async def _projects(self) -> list[str]:
        cfg = self._cfg("projects")
        if cfg:
            return list(cfg)
        projs = await asyncio.to_thread(self._jira.projects)
        return [p["key"] for p in (projs or [])]

    def object_kind_of(self, path: str) -> ObjectKind:
        if path.endswith(".jsonl"):
            return "record_collection"
        return "directory"

    async def stat(self, path: str) -> PathStat:
        if path.endswith(".jsonl"):
            return PathStat(path=path, type="file", media_type="application/x-ndjson",
                            fingerprint=await self.fingerprint(path), extra={"lazy": True})
        return PathStat(path=path, type="dir")

    async def list(self, path: str) -> list[Entry]:
        parts = self._parts(path)
        if len(parts) == 0:
            return [Entry("projects", "dir"),
                    Entry("users.jsonl", "file", "application/x-ndjson", extra={"lazy": True})]
        if len(parts) == 1 and parts[0] == "projects":
            return [Entry(p, "dir") for p in await self._projects()]
        if len(parts) == 2 and parts[0] == "projects":
            return [Entry("issues.jsonl", "file", "application/x-ndjson", extra={"lazy": True})]
        return []

    def _flatten_issue(self, issue: dict) -> dict:
        f = issue.get("fields", {}) or {}
        return {
            "key": issue.get("key"), "id": issue.get("id"),
            "summary": f.get("summary"), "description": f.get("description"),
            "status": (f.get("status") or {}).get("name"),
            "priority": (f.get("priority") or {}).get("name"),
            "assignee": (f.get("assignee") or {}).get("displayName"),
            "reporter": (f.get("reporter") or {}).get("displayName"),
            "labels": f.get("labels"), "created": f.get("created"), "updated": f.get("updated"),
        }

    async def read_records(self, path: str, range: Optional[Range] = None) -> AsyncIterator[dict]:
        parts = self._parts(path)
        if len(parts) == 3 and parts[0] == "projects" and parts[2] == "issues.jsonl":
            jql = f'project = "{parts[1]}" ORDER BY updated DESC'
            start, page = 0, 100
            limit = self._cfg("max_read_rows", 100000)
            while start < limit:
                res = await asyncio.to_thread(self._jira.jql, jql, start=start, limit=page)
                issues = (res or {}).get("issues", [])
                if not issues:
                    break
                for it in issues:
                    yield self._flatten_issue(it)
                if len(issues) < page:
                    break
                start += page
        elif len(parts) == 1 and parts[0] == "users.jsonl":
            users = await asyncio.to_thread(self._jira.get_all_users, limit=1000) if hasattr(self._jira, "get_all_users") else []
            for u in (users or []):
                yield dict(u)

    async def fingerprint(self, path: str) -> Optional[str]:
        parts = self._parts(path)
        if len(parts) == 3 and parts[0] == "projects" and parts[2] == "issues.jsonl":
            res = await asyncio.to_thread(self._jira.jql, f'project = "{parts[1]}"', start=0, limit=1)
            return f"total:{(res or {}).get('total', 0)}"
        return None

    async def sync(self, opts: SyncOptions) -> AsyncIterator[ObjectChange]:
        self.ctx.declare_enumeration("full")
        old = await self.state.get("objects") or {}
        seen: dict[str, str] = {}
        for proj in await self._projects():
            p = f"/projects/{proj}/issues.jsonl"
            fp = await self.fingerprint(p) or ""
            seen[p] = fp
            if opts.full or old.get(p) != fp:
                yield ObjectChange(p, "modified" if p in old else "added")
        for p in set(old) - set(seen):
            yield ObjectChange(p, "deleted")
        await self.state.set("objects", seen)
