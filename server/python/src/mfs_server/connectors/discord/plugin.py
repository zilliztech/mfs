"""Discord connector — text channels + sub-threads as message_stream.

Discord REST API v10 via httpx (no gateway). Bot auth header
`Authorization: Bot <token>`.

URI layout:

  /channels/<name>__<id>/messages.jsonl               top-level channel messages
  /channels/<name>__<id>/threads/<name>__<id>/messages.jsonl
                                                       sub-thread channel messages

Threads in Discord are first-class child channels (type=10/11/12) with
their own ids — NOT a per-message field. We enumerate active threads in
the guild via GET /guilds/{guild_id}/threads/active (one call, returns all
non-archived threads with their parent_id), then nest them under their
parent channel directory.

Archived threads are NOT enumerated in this version (the API requires
per-parent pagination via /channels/{id}/threads/archived/public). A
follow-up can add them behind a config opt-in for guilds with heavy
thread archives.

API endpoints used (all verified against Discord developer docs):
  GET /guilds/{guild_id}/channels                  -> parent channel list
  GET /guilds/{guild_id}/threads/active            -> all active threads
  GET /channels/{id}/messages?limit=100&before=<id> -> messages (desc, max 100/page)
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
# Text channel types (parent dirs we expose).
#   0  GUILD_TEXT
#   5  GUILD_ANNOUNCEMENT
_TEXT_TYPES = {0, 5}
# Thread channel types (children we nest under their parent).
#   10 ANNOUNCEMENT_THREAD
#   11 PUBLIC_THREAD
#   12 PRIVATE_THREAD  (only visible if the bot was invited)
_THREAD_TYPES = {10, 11, 12}


def _sanitize(name: str) -> str:
    return _SANITIZE.sub("-", name or "").strip("-") or "unnamed"


class DiscordPlugin(ConnectorPlugin):
    NAME = "discord"
    URI_SCHEME = "discord"
    DISPLAY_NAME = "Discord"
    PROMPT = (
        "Discord guild text channels + their threads as "
        "/channels/<name>__<id>/messages.jsonl and "
        "/channels/<name>__<id>/threads/<name>__<id>/messages.jsonl."
    )
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
        """Top-level text + announcement channels."""
        guild = self._cfg("guild_id")
        async with httpx.AsyncClient(headers=self._headers(), timeout=30) as c:
            resp = await c.get(f"{API}/guilds/{guild}/channels")
            resp.raise_for_status()
            return [ch for ch in resp.json() if ch.get("type") in _TEXT_TYPES]

    async def _active_threads_by_parent(self) -> dict[str, list[dict]]:
        """All currently-active threads in the guild, grouped by their
        `parent_id`. Single REST call regardless of channel count."""
        guild = self._cfg("guild_id")
        async with httpx.AsyncClient(headers=self._headers(), timeout=30) as c:
            resp = await c.get(f"{API}/guilds/{guild}/threads/active")
            resp.raise_for_status()
            data = resp.json()
        by_parent: dict[str, list[dict]] = {}
        for th in data.get("threads", []):
            if th.get("type") not in _THREAD_TYPES:
                continue
            pid = th.get("parent_id")
            if not pid:
                continue
            by_parent.setdefault(pid, []).append(th)
        return by_parent

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
        # /channels/<parent>: messages.jsonl + (optional) threads/ subdir
        if len(parts) == 2 and parts[0] == "channels":
            parent_id = self._channel_id(parts[1])
            entries: list[Entry] = [
                Entry("messages.jsonl", "file", "application/x-ndjson", extra={"lazy": True}),
            ]
            threads = (await self._active_threads_by_parent()).get(parent_id, [])
            if threads:
                entries.append(Entry("threads", "dir"))
            return entries
        # /channels/<parent>/threads: one dir per active thread
        if len(parts) == 3 and parts[0] == "channels" and parts[2] == "threads":
            parent_id = self._channel_id(parts[1])
            threads = (await self._active_threads_by_parent()).get(parent_id, [])
            return [Entry(self._dir_name(t), "dir") for t in threads]
        # /channels/<parent>/threads/<thread>: just messages.jsonl
        if len(parts) == 4 and parts[0] == "channels" and parts[2] == "threads":
            return [Entry("messages.jsonl", "file", "application/x-ndjson", extra={"lazy": True})]
        return []

    def _resolve_message_channel(self, parts: list[str]) -> Optional[str]:
        """Pick the channel id whose messages.jsonl a path points to.
        Both parent-channel messages and thread-channel messages go through
        the same Discord API; threads are themselves channels."""
        if len(parts) == 3 and parts[0] == "channels" and parts[2] == "messages.jsonl":
            return self._channel_id(parts[1])
        if (
            len(parts) == 5
            and parts[0] == "channels"
            and parts[2] == "threads"
            and parts[4] == "messages.jsonl"
        ):
            return self._channel_id(parts[3])
        return None

    async def read_records(self, path: str, range: Optional[Range] = None) -> AsyncIterator[dict]:
        parts = self._parts(path)
        channel = self._resolve_message_channel(parts)
        if channel is None:
            return
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
                    if n >= limit:
                        break  # honour max_read_rows mid-page (each page is up to 100)
                if n >= limit:
                    break
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
        threads_by_parent = await self._active_threads_by_parent()
        for ch in await self._channels():
            parent_dir = self._dir_name(ch)
            p = f"/channels/{parent_dir}/messages.jsonl"
            seen[p] = ""
            yield ObjectChange(p, "modified" if p in old else "added")
            # Nest the parent's active threads.
            for th in threads_by_parent.get(ch["id"], []):
                tp = f"/channels/{parent_dir}/threads/{self._dir_name(th)}/messages.jsonl"
                seen[tp] = ""
                yield ObjectChange(tp, "modified" if tp in old else "added")
        for p in set(old) - set(seen):
            yield ObjectChange(p, "deleted")
        await self.state.set("objects", seen)
