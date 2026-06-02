"""Slack connector — message_stream; per_group thread_aggregate.
slack_sdk AsyncWebClient. Layout /channels/<name>__<id>/messages.jsonl + /users.jsonl.
Each channel's messages.jsonl is a message_stream; the framework groups by thread_ts
into thread_aggregate chunks (see engine message_stream pipeline). Per-day sharding
from the catalog is a future optimization; one stream per channel maps cleanly to
per-thread aggregation.

API verified against slack_sdk WebClient docs: AsyncWebClient(token); await
client.conversations_list(types, cursor) -> resp["channels"] + resp["response_metadata"]
["next_cursor"]; conversations_history(channel, cursor, limit, oldest); users_list(cursor)
-> resp["members"]. NOT end-to-end tested (needs a bot token).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Optional

from slack_sdk.web.async_client import AsyncWebClient

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

_SANITIZE = re.compile(r"[^a-zA-Z0-9_.-]+")


def _sanitize(name: str) -> str:
    return _SANITIZE.sub("-", name or "").strip("-") or "unnamed"


class SlackPlugin(ConnectorPlugin):
    NAME = "slack"
    URI_SCHEME = "slack"
    DISPLAY_NAME = "Slack"
    PROMPT = "Slack channels as /channels/<name>__<id>/messages.jsonl + users.jsonl."
    CAPABILITIES = Capabilities(
        manual_sync=True,
        watch=False,
        cursor_kind="ts",
        full_scan=True,
        delete_detection="never",
        paged_cat=True,
    )

    def __init__(self, config, credential, *, ctx):
        super().__init__(config, credential, ctx=ctx)
        self._client: Optional[AsyncWebClient] = None

    def _cfg(self, k, d=None):
        return (
            self.config.get(k, d) if isinstance(self.config, dict) else getattr(self.config, k, d)
        )

    async def connect(self) -> None:
        self._client = AsyncWebClient(token=self._cfg("token") or self.credential)

    async def healthcheck(self) -> HealthStatus:
        try:
            await self._client.auth_test()
            return HealthStatus(ok=True)
        except Exception as e:  # noqa: BLE001
            return HealthStatus(ok=False, detail=str(e))

    def _parts(self, path: str) -> list[str]:
        return [p for p in path.strip("/").split("/") if p]

    async def _channels(self) -> list[dict]:
        out, cursor = [], None
        types = self._cfg("channel_types", "public_channel")
        while True:
            resp = await self._client.conversations_list(types=types, cursor=cursor, limit=200)
            out.extend(resp["channels"])
            cursor = (resp.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
        return out

    @staticmethod
    def _dir_name(ch: dict) -> str:
        return f"{_sanitize(ch.get('name'))}__{ch['id']}"

    @staticmethod
    def _channel_id(dir_name: str) -> str:
        return dir_name.rsplit("__", 1)[-1]

    def preset_for(self, path: str):
        if path.endswith("messages.jsonl"):
            return "slack.messages"
        if path.endswith("/users.jsonl") or path == "/users.jsonl":
            return "slack.users"
        return None

    def object_kind_of(self, path: str) -> ObjectKind:
        if path.endswith("messages.jsonl"):
            return "message_stream"
        if path.endswith("users.jsonl"):
            return "record_collection"
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
            return [
                Entry("channels", "dir"),
                Entry("users.jsonl", "file", "application/x-ndjson", extra={"lazy": True}),
            ]
        if len(parts) == 1 and parts[0] == "channels":
            return [Entry(self._dir_name(c), "dir") for c in await self._channels()]
        if len(parts) == 2 and parts[0] == "channels":
            return [Entry("messages.jsonl", "file", "application/x-ndjson", extra={"lazy": True})]
        return []

    async def read_records(self, path: str, range: Optional[Range] = None) -> AsyncIterator[dict]:
        parts = self._parts(path)
        if len(parts) == 3 and parts[0] == "channels" and parts[2] == "messages.jsonl":
            channel = self._channel_id(parts[1])
            oldest = self._cfg("oldest")  # optional unix ts lower bound
            limit = self._cfg("max_read_rows", 50000)
            n, cursor = 0, None
            while n < limit:
                resp = await self._client.conversations_history(
                    channel=channel, cursor=cursor, limit=200, oldest=oldest
                )
                for m in resp["messages"]:
                    # ensure thread grouping key: thread_ts present for replies, else own ts
                    m.setdefault("thread_ts", m.get("ts"))
                    yield m
                    n += 1
                    if n >= limit:
                        break  # honour max_read_rows mid-page (each page is up to 200)
                if n >= limit:
                    break
                if not resp.get("has_more"):
                    break
                cursor = (resp.get("response_metadata") or {}).get("next_cursor")
                if not cursor:
                    break
            if n >= limit:
                self.ctx.declare_partial(path)  # hit max_read_rows -> partial recall
        elif len(parts) == 1 and parts[0] == "users.jsonl":
            cursor = None
            while True:
                resp = await self._client.users_list(cursor=cursor, limit=200)
                for u in resp["members"]:
                    yield u
                cursor = (resp.get("response_metadata") or {}).get("next_cursor")
                if not cursor:
                    break

    async def fingerprint(self, path: str) -> Optional[str]:
        return None

    async def sync(self, opts: SyncOptions) -> AsyncIterator[ObjectChange]:
        self.ctx.declare_enumeration("full")
        old = await self.state.get("objects") or {}
        seen: dict[str, str] = {}
        # Channel message streams.
        for ch in await self._channels():
            p = f"/channels/{self._dir_name(ch)}/messages.jsonl"
            seen[p] = ""
            yield ObjectChange(p, "modified" if p in old else "added")
        # Workspace user directory — small and high-value for "who is X?"
        # searches. preset_for("/users.jsonl") applies slack.users, indexing
        # each member as a row_text chunk (name, real_name, title, email).
        users_p = "/users.jsonl"
        seen[users_p] = ""
        yield ObjectChange(users_p, "modified" if users_p in old else "added")
        for p in set(old) - set(seen):
            yield ObjectChange(p, "deleted")
        await self.state.set("objects", seen)
