"""Linear connector — record_collection.
Linear has no official Python SDK; it is a GraphQL API (httpx). Endpoint
https://api.linear.app/graphql, auth header `Authorization: <personal-api-key>`
(raw key, NOT 'Bearer'; OAuth tokens would use Bearer). Cursor pagination via
`pageInfo { hasNextPage endCursor }` + `first/after` on connections.

Layout /teams/<team>/issues.jsonl + /users.jsonl. API shape verified against
Linear developer docs. NOT end-to-end tested (needs an API key).
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

ENDPOINT = "https://api.linear.app/graphql"

_TEAMS_Q = "query { teams { nodes { id key name } } }"

_ISSUES_Q = """
query($teamId: String!, $after: String) {
  team(id: $teamId) {
    issues(first: 100, after: $after) {
      nodes {
        identifier title description priority
        state { name } assignee { name } labels { nodes { name } }
        createdAt updatedAt
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""


class LinearPlugin(ConnectorPlugin):
    NAME = "linear"
    URI_SCHEME = "linear"
    DISPLAY_NAME = "Linear"
    PROMPT = "Linear issues as /teams/<team>/issues.jsonl + users.jsonl."
    CAPABILITIES = Capabilities(
        manual_sync=True,
        watch=False,
        cursor_kind="updatedAt",
        full_scan=True,
        delete_detection="full_scan",
        paged_cat=True,
    )

    def _cfg(self, k, d=None):
        return (
            self.config.get(k, d) if isinstance(self.config, dict) else getattr(self.config, k, d)
        )

    def _headers(self) -> dict:
        key = self._cfg("api_key") or self.credential
        return {"Authorization": key, "Content-Type": "application/json"}

    async def _gql(self, query: str, variables: Optional[dict] = None) -> dict:
        async with httpx.AsyncClient(headers=self._headers(), timeout=30) as c:
            resp = await c.post(ENDPOINT, json={"query": query, "variables": variables or {}})
            data = resp.json()
            if "errors" in data:
                raise RuntimeError(str(data["errors"]))
            return data["data"]

    async def healthcheck(self) -> HealthStatus:
        try:
            await self._gql("query { viewer { id } }")
            return HealthStatus(ok=True)
        except Exception as e:  # noqa: BLE001
            return HealthStatus(ok=False, detail=str(e))

    def _parts(self, path: str) -> list[str]:
        return [p for p in path.strip("/").split("/") if p]

    async def _teams(self) -> list[dict]:
        data = await self._gql(_TEAMS_Q)
        nodes = data["teams"]["nodes"]
        cfg = self._cfg("teams")
        if cfg:
            nodes = [t for t in nodes if t["key"] in cfg or t["id"] in cfg]
        return nodes

    def object_kind_of(self, path: str) -> ObjectKind:
        if path.endswith(".jsonl"):
            return "record_collection"
        return "directory"

    async def stat(self, path: str) -> PathStat:
        if path.endswith(".jsonl"):
            return PathStat(
                path=path,
                type="file",
                media_type="application/x-ndjson",
                fingerprint=None,
                extra={"lazy": True},
            )
        return PathStat(path=path, type="dir")

    async def list(self, path: str) -> list[Entry]:
        parts = self._parts(path)
        if len(parts) == 0:
            return [
                Entry("teams", "dir"),
                Entry("users.jsonl", "file", "application/x-ndjson", extra={"lazy": True}),
            ]
        if len(parts) == 1 and parts[0] == "teams":
            return [Entry(t["key"], "dir") for t in await self._teams()]
        if len(parts) == 2 and parts[0] == "teams":
            return [Entry("issues.jsonl", "file", "application/x-ndjson", extra={"lazy": True})]
        return []

    @staticmethod
    def _flatten(node: dict) -> dict:
        return {
            "identifier": node.get("identifier"),
            "title": node.get("title"),
            "description": node.get("description"),
            "priority": node.get("priority"),
            "state": (node.get("state") or {}).get("name"),
            "assignee": (node.get("assignee") or {}).get("name"),
            "labels": [n["name"] for n in (node.get("labels") or {}).get("nodes", [])],
            "createdAt": node.get("createdAt"),
            "updatedAt": node.get("updatedAt"),
        }

    async def _team_id(self, key: str) -> Optional[str]:
        for t in await self._teams():
            if t["key"] == key or t["id"] == key:
                return t["id"]
        return None

    async def read_records(self, path: str, range: Optional[Range] = None) -> AsyncIterator[dict]:
        parts = self._parts(path)
        if len(parts) == 3 and parts[0] == "teams" and parts[2] == "issues.jsonl":
            team_id = await self._team_id(parts[1])
            if not team_id:
                return
            after = None
            while True:
                data = await self._gql(_ISSUES_Q, {"teamId": team_id, "after": after})
                conn = data["team"]["issues"]
                for n in conn["nodes"]:
                    yield self._flatten(n)
                if not conn["pageInfo"]["hasNextPage"]:
                    break
                after = conn["pageInfo"]["endCursor"]
        elif len(parts) == 1 and parts[0] == "users.jsonl":
            data = await self._gql("query { users { nodes { id name email active } } }")
            for u in data["users"]["nodes"]:
                yield u

    async def fingerprint(self, path: str) -> Optional[str]:
        return None  # GraphQL issue count needs a separate query; full_scan diff covers it

    async def sync(self, opts: SyncOptions) -> AsyncIterator[ObjectChange]:
        self.ctx.declare_enumeration("full")
        old = await self.state.get("objects") or {}
        seen: dict[str, str] = {}
        for t in await self._teams():
            p = f"/teams/{t['key']}/issues.jsonl"
            seen[p] = ""
            yield ObjectChange(p, "modified" if p in old else "added")
        for p in set(old) - set(seen):
            yield ObjectChange(p, "deleted")
        await self.state.set("objects", seen)
