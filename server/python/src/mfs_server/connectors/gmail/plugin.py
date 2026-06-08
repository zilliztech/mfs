"""Gmail connector — message_stream; per_group thread_aggregate
(grouped by threadId). google-api-python-client (sync; wrapped in asyncio.to_thread).
Layout /labels/<label>/messages.jsonl. One stream per label; the framework groups by
threadId into thread_aggregate chunks.

API verified against google-api-python-client Gmail v1 docs: service = build('gmail',
'v1', credentials=creds); service.users().labels().list(userId='me'); .messages().list(
userId, labelIds, pageToken, q) -> {'messages':[{'id','threadId'}], 'nextPageToken'};
.messages().get(userId, id, format='full') -> full message. NOT end-to-end tested.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
from collections.abc import AsyncIterator
from typing import Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

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
    return _SANITIZE.sub("-", name or "").strip("-").lower() or "label"


def _decode_body(payload: dict) -> str:
    """Walk MIME parts, return first text/plain (fallback text/html stripped)."""

    def walk(part) -> str:
        mt = part.get("mimeType", "")
        body = part.get("body", {})
        data = body.get("data")
        if mt == "text/plain" and data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        for sub in part.get("parts", []) or []:
            t = walk(sub)
            if t:
                return t
        if mt == "text/html" and data:
            html = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            return re.sub(r"<[^>]+>", " ", html)
        return ""

    return walk(payload or {})


class GmailPlugin(ConnectorPlugin):
    NAME = "gmail"
    URI_SCHEME = "gmail"
    DISPLAY_NAME = "Gmail"
    PROMPT = "Gmail labels as /labels/<label>/messages.jsonl (grouped into threads)."
    CAPABILITIES = Capabilities(
        manual_sync=True,
        watch=False,
        cursor_kind="historyId",
        full_scan=True,
        delete_detection="never",
        paged_cat=True,
    )

    def __init__(self, config, credential, *, ctx):
        super().__init__(config, credential, ctx=ctx)
        self._svc = None

    def _cfg(self, k, d=None):
        return (
            self.config.get(k, d) if isinstance(self.config, dict) else getattr(self.config, k, d)
        )

    async def connect(self) -> None:
        def build_svc():
            tok = self._cfg("token") or {}
            # `file:` refs resolve to file text via engine._resolve_ref. Try to
            # JSON-parse so a token.json mounted from disk works the same as an
            # inline TOML table; fall back to treating the string as a bare
            # access token (the other documented form).
            if isinstance(tok, str):
                try:
                    tok = json.loads(tok)
                except json.JSONDecodeError:
                    pass
            creds = (
                Credentials.from_authorized_user_info(tok)
                if isinstance(tok, dict)
                else Credentials(token=tok)
            )
            return build("gmail", "v1", credentials=creds, cache_discovery=False)

        self._svc = await asyncio.to_thread(build_svc)

    async def healthcheck(self) -> HealthStatus:
        try:
            await asyncio.to_thread(lambda: self._svc.users().getProfile(userId="me").execute())
            return HealthStatus(ok=True)
        except Exception as e:  # noqa: BLE001
            return HealthStatus(ok=False, detail=str(e))

    def _parts(self, path: str) -> list[str]:
        return [p for p in path.strip("/").split("/") if p]

    async def _labels(self) -> list[dict]:
        cfg = self._cfg("labels")
        resp = await asyncio.to_thread(
            lambda: self._svc.users().labels().list(userId="me").execute()
        )
        labels = resp.get("labels", [])
        if cfg:
            labels = [lb for lb in labels if lb["name"] in cfg or lb["id"] in cfg]
        return labels

    def preset_for(self, path: str):
        return "gmail.messages" if path.endswith("messages.jsonl") else None

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
            return [Entry("labels", "dir")]
        if len(parts) == 1 and parts[0] == "labels":
            return [
                Entry(f"{_sanitize(lb['name'])}__{lb['id']}", "dir") for lb in await self._labels()
            ]
        if len(parts) == 2 and parts[0] == "labels":
            return [Entry("messages.jsonl", "file", "application/x-ndjson", extra={"lazy": True})]
        return []

    def _flatten(self, msg: dict) -> dict:
        payload = msg.get("payload", {}) or {}
        headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
        return {
            "id": msg.get("id"),
            "threadId": msg.get("threadId"),
            "subject": headers.get("subject"),
            "from": headers.get("from"),
            "to": headers.get("to"),
            "date": headers.get("date"),
            "snippet": msg.get("snippet"),
            "body": _decode_body(payload),
        }

    async def read_records(self, path: str, range: Optional[Range] = None) -> AsyncIterator[dict]:
        parts = self._parts(path)
        if len(parts) == 3 and parts[0] == "labels" and parts[2] == "messages.jsonl":
            label_id = parts[1].rsplit("__", 1)[-1]
            limit = self._cfg("max_read_rows", 20000)
            n, page_token = 0, None
            while n < limit:
                resp = await asyncio.to_thread(
                    lambda pt=page_token: (
                        self._svc.users()
                        .messages()
                        .list(userId="me", labelIds=[label_id], pageToken=pt, maxResults=100)
                        .execute()
                    )
                )
                for ref in resp.get("messages", []):
                    full = await asyncio.to_thread(
                        lambda mid=ref["id"]: (
                            self._svc.users()
                            .messages()
                            .get(userId="me", id=mid, format="full")
                            .execute()
                        )
                    )
                    yield self._flatten(full)
                    n += 1
                page_token = resp.get("nextPageToken")
                if not page_token:
                    break
            if n >= limit:
                self.ctx.declare_partial(path)  # hit max_read_rows -> partial recall

    async def fingerprint(self, path: str) -> Optional[str]:
        return None

    async def sync(self, opts: SyncOptions) -> AsyncIterator[ObjectChange]:
        self.ctx.declare_enumeration("full")
        old = await self.state.get("objects") or {}
        seen: dict[str, str] = {}
        for lb in await self._labels():
            p = f"/labels/{_sanitize(lb['name'])}__{lb['id']}/messages.jsonl"
            seen[p] = ""
            yield ObjectChange(p, "modified" if p in old else "added")
        for p in set(old) - set(seen):
            yield ObjectChange(p, "deleted")
        await self.state.set("objects", seen)
