"""Discord connector — message_stream.
Discord REST API v10 via httpx (no gateway). Bot auth header
`Authorization: Bot <token>`. Layout /channels/<name>__<id>/messages.jsonl.

API verified against Discord developer docs: GET /guilds/{guild_id}/channels ->
channel list; GET /channels/{id}/messages?limit=100&before=<id> -> messages
(descending, max 100/page, paginate with `before`=oldest id). NOT end-to-end tested.
"""

from __future__ import annotations

import re
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

API = "https://discord.com/api/v10"
_SANITIZE = re.compile(r"[^a-zA-Z0-9_.-]+")
# text channel types: 0 = GUILD_TEXT, 5 = GUILD_ANNOUNCEMENT
_TEXT_TYPES = {0, 5}


def _sanitize(name: str) -> str:
    return _SANITIZE.sub("-", name or "").strip("-") or "unnamed"


class DiscordPlugin(ConnectorPlugin):
    NAME = "discord"
    URI_SCHEME = "discord"
    DISPLAY_NAME = "Discord"
    PROMPT = "Discord text channels as /channels/<name>__<id>/messages.jsonl."
    CAPABILITIES = Capabilities(
        manual_sync=True,
        watch=False,
        cursor_kind="message_id",
        full_scan=True,
        delete_detection="never",
        paged_cat=True,
    )

    def _cfg(self, k, d=None):
        return (
            self.config.get(k, d) if isinstance(self.config, dict) else getattr(self.config, k, d)
        )

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bot {self._cfg('token') or self.credential}",
            "User-Agent": "mfs-discord/0.4",
        }

    async def healthcheck(self) -> HealthStatus:
        try:
            async with httpx.AsyncClient(headers=self._headers(), timeout=30) as c:
                r = await c.get(f"{API}/users/@me")
                r.raise_for_status()
            return HealthStatus(ok=True)
        except Exception as e:  # noqa: BLE001
            return HealthStatus(ok=False, detail=str(e))

    def _parts(self, path: str) -> list[str]:
        return [p for p in path.strip("/").split("/") if p]

    async def _channels(self) -> list[dict]:
        guild = self._cfg("guild_id")
        async with httpx.AsyncClient(headers=self._headers(), timeout=30) as c:
            resp = await c.get(f"{API}/guilds/{guild}/channels")
            resp.raise_for_status()
            return [ch for ch in resp.json() if ch.get("type") in _TEXT_TYPES]

    @staticmethod
    def _dir_name(ch: dict) -> str:
        return f"{_sanitize(ch.get('name'))}__{ch['id']}"

    @staticmethod
    def _channel_id(dir_name: str) -> str:
        return dir_name.rsplit("__", 1)[-1]

    def preset_for(self, path: str):
        return "discord.messages" if path.endswith("messages.jsonl") else None

    def object_kind_of(self, path: str) -> ObjectKind:
        if path.endswith("messages.jsonl"):
            return "message_stream"
        return "directory"

    async def stat(self, path: str) -> PathStat:
        if path.endswith(".jsonl"):
            return PathStat(
                path=path, type="file", media_type="application/x-ndjson", extra={"lazy": True}
            )
        return PathStat(path=path, type="dir")

    async def list(self, path: str) -> list[Entry]:
        parts = self._parts(path)
        if len(parts) == 0:
            return [Entry("channels", "dir")]
        if len(parts) == 1 and parts[0] == "channels":
            return [Entry(self._dir_name(c), "dir") for c in await self._channels()]
        if len(parts) == 2 and parts[0] == "channels":
            return [Entry("messages.jsonl", "file", "application/x-ndjson", extra={"lazy": True})]
        return []

    async def read_records(self, path: str, range: Optional[Range] = None) -> AsyncIterator[dict]:
        parts = self._parts(path)
        if len(parts) == 3 and parts[0] == "channels" and parts[2] == "messages.jsonl":
            channel = self._channel_id(parts[1])
            limit = self._cfg("max_read_rows", 50000)
            n, before = 0, None
            async with httpx.AsyncClient(headers=self._headers(), timeout=60) as c:
                while n < limit:
                    params = {"limit": 100}
                    if before:
                        params["before"] = before
                    resp = await c.get(f"{API}/channels/{channel}/messages", params=params)
                    resp.raise_for_status()
                    msgs = resp.json()
                    if not msgs:
                        break
                    for m in msgs:
                        yield m
                        n += 1
                    before = msgs[-1]["id"]  # oldest id of this (descending) page
                    if len(msgs) < 100:
                        break
            if n >= limit:
                self.ctx.declare_partial(path)  # hit max_read_rows -> partial recall

    async def fingerprint(self, path: str) -> Optional[str]:
        return None

    async def sync(self, opts: SyncOptions) -> AsyncIterator[ObjectChange]:
        self.ctx.declare_enumeration("full")
        old = await self.state.get("objects") or {}
        seen: dict[str, str] = {}
        for ch in await self._channels():
            p = f"/channels/{self._dir_name(ch)}/messages.jsonl"
            seen[p] = ""
            yield ObjectChange(p, "modified" if p in old else "added")
        for p in set(old) - set(seen):
            yield ObjectChange(p, "deleted")
        await self.state.set("objects", seen)
