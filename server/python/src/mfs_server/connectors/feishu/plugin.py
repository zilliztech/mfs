"""Feishu / Lark connector — group chats as message_stream;
per_group thread_aggregate. lark-oapi official SDK (sync builder API; wrapped in
asyncio.to_thread). Layout /chats/<name>__<id>/messages.jsonl.

API verified against lark-oapi docs: client = lark.Client.builder().app_id().app_secret()
.build(); ListChatRequest.builder().build() -> client.im.v1.chat.list(req); resp.success(),
resp.data.items (chat_id, name). ListMessageRequest.builder().container_id_type("chat")
.container_id(chat_id).page_token(...).build() -> client.im.v1.message.list(req);
resp.data.items (message_id, body.content, msg_type, create_time, sender), page_token,
has_more. NOT end-to-end tested (needs app_id/app_secret).
"""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from typing import Optional

import lark_oapi as lark
from lark_oapi.api.im.v1 import ListChatRequest, ListMessageRequest

from ..base import (
    Capabilities, ConnectorPlugin, Entry, HealthStatus, ObjectChange, ObjectKind,
    PathStat, Range, SyncOptions,
)

_SANITIZE = re.compile(r"[^a-zA-Z0-9_.-]+")


def _sanitize(name: str) -> str:
    return _SANITIZE.sub("-", name or "").strip("-") or "chat"


def _extract_text(msg_type: str, content: str) -> str:
    """body.content is a JSON string; pull human text for common msg types."""
    try:
        data = json.loads(content) if content else {}
    except (ValueError, TypeError):
        return content or ""
    if msg_type == "text":
        return data.get("text", "")
    if msg_type == "post":          # rich text
        out = []
        for block in (data.get("content") or []):
            for el in block:
                if isinstance(el, dict) and el.get("text"):
                    out.append(el["text"])
        return " ".join(out)
    return data.get("text") or content or ""


class FeishuPlugin(ConnectorPlugin):
    NAME = "feishu"
    URI_SCHEME = "feishu"
    DISPLAY_NAME = "Feishu / Lark"
    PROMPT = "Feishu group chats as /chats/<name>__<id>/messages.jsonl."
    CAPABILITIES = Capabilities(manual_sync=True, watch=False, cursor_kind="create_time",
                                full_scan=True, delete_detection="never", paged_cat=True)

    def __init__(self, config, credential, *, ctx):
        super().__init__(config, credential, ctx=ctx)
        self._client = None

    def _cfg(self, k, d=None):
        return self.config.get(k, d) if isinstance(self.config, dict) else getattr(self.config, k, d)

    async def connect(self) -> None:
        def build():
            return lark.Client.builder() \
                .app_id(self._cfg("app_id")) \
                .app_secret(self._cfg("app_secret") or self.credential) \
                .build()
        self._client = await asyncio.to_thread(build)

    async def healthcheck(self) -> HealthStatus:
        try:
            await self._chats()
            return HealthStatus(ok=True)
        except Exception as e:  # noqa: BLE001
            return HealthStatus(ok=False, detail=str(e))

    def _parts(self, path: str) -> list[str]:
        return [p for p in path.strip("/").split("/") if p]

    async def _chats(self) -> list[dict]:
        def run():
            req = ListChatRequest.builder().build()
            resp = self._client.im.v1.chat.list(req)
            if not resp.success():
                raise RuntimeError(f"feishu chat.list failed: code={resp.code} msg={resp.msg}")
            return [{"chat_id": c.chat_id, "name": c.name} for c in (resp.data.items or [])]
        return await asyncio.to_thread(run)

    @staticmethod
    def _dir_name(chat: dict) -> str:
        return f"{_sanitize(chat.get('name'))}__{chat['chat_id']}"

    @staticmethod
    def _chat_id(dir_name: str) -> str:
        return dir_name.rsplit("__", 1)[-1]

    def object_kind_of(self, path: str) -> ObjectKind:
        if path.endswith("messages.jsonl"):
            return "message_stream"
        return "directory"

    async def stat(self, path: str) -> PathStat:
        if path.endswith(".jsonl"):
            return PathStat(path=path, type="file", media_type="application/x-ndjson", extra={"lazy": True})
        return PathStat(path=path, type="dir")

    async def list(self, path: str) -> list[Entry]:
        parts = self._parts(path)
        if len(parts) == 0:
            return [Entry("chats", "dir")]
        if len(parts) == 1 and parts[0] == "chats":
            return [Entry(self._dir_name(c), "dir") for c in await self._chats()]
        if len(parts) == 2 and parts[0] == "chats":
            return [Entry("messages.jsonl", "file", "application/x-ndjson", extra={"lazy": True})]
        return []

    async def read_records(self, path: str, range: Optional[Range] = None) -> AsyncIterator[dict]:
        parts = self._parts(path)
        if len(parts) == 3 and parts[0] == "chats" and parts[2] == "messages.jsonl":
            chat_id = self._chat_id(parts[1])
            limit = self._cfg("max_read_rows", 50000)
            n, page_token = 0, None

            def fetch(pt):
                b = ListMessageRequest.builder().container_id_type("chat").container_id(chat_id)
                if pt:
                    b = b.page_token(pt)
                resp = self._client.im.v1.message.list(b.build())
                if not resp.success():
                    raise RuntimeError(f"feishu message.list failed: code={resp.code} msg={resp.msg}")
                return resp.data

            while n < limit:
                data = await asyncio.to_thread(fetch, page_token)
                for it in (data.items or []):
                    msg_type = it.msg_type
                    content = it.body.content if it.body else ""
                    yield {
                        "message_id": it.message_id, "msg_type": msg_type,
                        "create_time": it.create_time,
                        "sender": getattr(it.sender, "id", None) if it.sender else None,
                        "thread_id": getattr(it, "thread_id", None) or getattr(it, "root_id", None),
                        "text": _extract_text(msg_type, content),
                    }
                    n += 1
                if not data.has_more:
                    break
                page_token = data.page_token

    async def fingerprint(self, path: str) -> Optional[str]:
        return None

    async def sync(self, opts: SyncOptions) -> AsyncIterator[ObjectChange]:
        self.ctx.declare_enumeration("full")
        old = await self.state.get("objects") or {}
        seen: dict[str, str] = {}
        for chat in await self._chats():
            p = f"/chats/{self._dir_name(chat)}/messages.jsonl"
            seen[p] = ""
            yield ObjectChange(p, "modified" if p in old else "added")
        for p in set(old) - set(seen):
            yield ObjectChange(p, "deleted")
        await self.state.set("objects", seen)
