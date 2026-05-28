"""Feishu / Lark connector — group chats as message_stream + docs as text.

Two subtrees in one connector:
  /chats/<name>__<chat-id>/messages.jsonl   group messages (lazy stream)
  /docs/<title>__<doc-token>.md             docx document body (rendered text)

Auth: tenant_access_token (bot). p2p single chats are NOT enumerable via REST
(documented Feishu limit on chat.list); docs require the bot to be a collaborator.

API endpoints used (all sync, wrapped in asyncio.to_thread):
  im.v1.chat.list                    -> the bot's group chats
  im.v1.message.list                 -> messages in one chat
  drive.v1.file.list                 -> docs/sheets/etc. accessible to the bot
  docx.v1.document.raw_content       -> plain-text body of a docx document
  docx.v1.document.get               -> document metadata (title, revision_id)
"""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from typing import Optional

import lark_oapi as lark
from lark_oapi.api.docx.v1 import GetDocumentRequest, RawContentDocumentRequest
from lark_oapi.api.drive.v1 import ListFileRequest
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
    PROMPT = ("Feishu group chats as /chats/<name>__<id>/messages.jsonl + "
              "docx documents as /docs/<title>__<doc-token>.md.")
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

    @staticmethod
    def _doc_name(doc: dict) -> str:
        return f"{_sanitize(doc.get('name'))}__{doc['token']}.md"

    @staticmethod
    def _doc_id(file_name: str) -> str:
        return file_name[: -len(".md")].rsplit("__", 1)[-1]

    async def _list_folder_docx(self, folder_token: str) -> list[dict]:
        """Recursively enumerate docx documents inside a Drive folder the bot has been
        shared on. Recurses into subfolders. The bot only sees `file.list` results for
        a folder it has explicit access to — Feishu does NOT auto-list "shared with me",
        so the user has to share the FOLDER (not individual docs) with the bot for this
        to surface anything."""
        docs: list[dict] = []
        subfolders: list[str] = []

        def fetch(pt):
            b = ListFileRequest.builder().folder_token(folder_token).page_size(100)
            if pt:
                b = b.page_token(pt)
            resp = self._client.drive.v1.file.list(b.build())
            if not resp.success():
                raise RuntimeError(
                    f"feishu drive.file.list(folder={folder_token}) failed: "
                    f"code={resp.code} msg={resp.msg}")
            return resp.data

        page_token = None
        while True:
            data = await asyncio.to_thread(fetch, page_token)
            for f in (data.files or []):
                t = getattr(f, "type", None)
                if t == "docx":
                    # only docx for now — sheets / bitable / mindnote etc. have very
                    # different read APIs and aren't usefully searchable as plain text.
                    docs.append({
                        "token": f.token,
                        "name": getattr(f, "name", None) or f.token,
                        "modified_time": getattr(f, "modified_time", None),
                    })
                elif t == "folder":
                    subfolders.append(f.token)
            if not getattr(data, "has_more", False):
                break
            page_token = getattr(data, "next_page_token", None)
            if not page_token:
                break

        # depth-first recurse into subfolders
        for sub in subfolders:
            docs.extend(await self._list_folder_docx(sub))
        return docs

    async def _docs(self) -> list[dict]:
        """Discover docx documents to index, via three optional inputs:

        1. `docs_folder_token` (str)  — recursively enumerate docs under one shared
           folder. Best UX: user shares the folder with bot once, then dropping a
           new doc in is auto-indexed on next sync.
        2. `extra_docs` (list[{token, label}]) — explicit per-doc list, for docs
           that live outside the folder. Each one must be shared with the bot
           individually ("..." -> "添加协作者").
        3. Neither set — returns empty. The bot's own drive root is intentionally
           NOT enumerated by default; on a fresh enterprise app it is empty, and
           silently returning bot-internal files would be confusing.

        Sources are de-duped by `token`. `extra_docs` wins on name conflicts."""
        out: dict[str, dict] = {}

        # 1) folder enumeration (recursive)
        folder_token = self._cfg("docs_folder_token") or ""
        if folder_token:
            for d in await self._list_folder_docx(folder_token):
                out[d["token"]] = d

        # 2) explicit extra_docs
        for extra in (self._cfg("extra_docs") or []):
            if not isinstance(extra, dict):
                continue
            tok = extra.get("token")
            if not tok:
                continue
            # fetch metadata so we have a title + revision_id for the dir name + fingerprint
            try:
                meta = await self._doc_meta(tok)
            except Exception as e:  # noqa: BLE001 — log but don't kill the whole sync
                meta = {"title": extra.get("label") or tok, "revision_id": str(e)[:32]}
            out[tok] = {
                "token": tok,
                "name": extra.get("label") or meta.get("title") or tok,
                # extras have no Drive modified_time; fall back to revision_id so any
                # edit (which bumps revision) triggers re-index.
                "modified_time": f"rev:{meta.get('revision_id')}",
            }
        return list(out.values())

    async def _doc_content(self, doc_id: str) -> str:
        """Fetch a docx document's body as plain text.

        Uses `docx.v1.document.raw_content` (returns the document body flattened
        to text — preserves line breaks, drops most formatting). For richer
        markdown we could walk `document_block.list` instead, but raw_content is
        enough for embedding-based search and is one API call vs N.
        """
        def fetch():
            req = RawContentDocumentRequest.builder().document_id(doc_id).build()
            resp = self._client.docx.v1.document.raw_content(req)
            if not resp.success():
                raise RuntimeError(
                    f"feishu docx.raw_content({doc_id}) failed: code={resp.code} msg={resp.msg}")
            return getattr(resp.data, "content", "") or ""
        return await asyncio.to_thread(fetch)

    async def _doc_meta(self, doc_id: str) -> dict:
        """Document metadata (title + revision_id) for fingerprinting."""
        def fetch():
            req = GetDocumentRequest.builder().document_id(doc_id).build()
            resp = self._client.docx.v1.document.get(req)
            if not resp.success():
                raise RuntimeError(
                    f"feishu docx.get({doc_id}) failed: code={resp.code} msg={resp.msg}")
            d = resp.data.document if resp.data else None
            return {
                "title": getattr(d, "title", None) if d else None,
                "revision_id": getattr(d, "revision_id", None) if d else None,
            }
        return await asyncio.to_thread(fetch)

    def object_kind_of(self, path: str) -> ObjectKind:
        if path.endswith("messages.jsonl"):
            return "message_stream"
        # NB: engine's indexing branches gate on "document" / "code" / "image" / etc.,
        # NOT on "text" — a plain text file with no recognised extension lands in
        # `text_blob` (grep-only, not embedded). Feishu docx body is prose, so this
        # is "document" (engine -> chunk_body via chonkie RecursiveChunker).
        if path.startswith("/docs/") and path.endswith(".md"):
            return "document"
        return "directory"

    async def stat(self, path: str) -> PathStat:
        if path.endswith(".jsonl"):
            return PathStat(path=path, type="file", media_type="application/x-ndjson", extra={"lazy": True})
        if path.startswith("/docs/") and path.endswith(".md"):
            return PathStat(path=path, type="file", media_type="text/markdown")
        return PathStat(path=path, type="dir")

    async def list(self, path: str) -> list[Entry]:
        parts = self._parts(path)
        if len(parts) == 0:
            return [Entry("chats", "dir"), Entry("docs", "dir")]
        if len(parts) == 1 and parts[0] == "chats":
            return [Entry(self._dir_name(c), "dir") for c in await self._chats()]
        if len(parts) == 2 and parts[0] == "chats":
            return [Entry("messages.jsonl", "file", "application/x-ndjson", extra={"lazy": True})]
        if len(parts) == 1 and parts[0] == "docs":
            return [Entry(self._doc_name(d), "file", "text/markdown") for d in await self._docs()]
        return []

    async def read(self, path: str, range: Optional[Range] = None) -> AsyncIterator[bytes]:
        if path.startswith("/docs/") and path.endswith(".md"):
            doc_id = self._doc_id(path.rsplit("/", 1)[-1])
            content = await self._doc_content(doc_id)
            yield content.encode("utf-8")
            return
        async for chunk in super().read(path, range):
            yield chunk

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
        # Docs: modified_time from Drive listing already in sync's `seen` map; the
        # actual revision check happens at sync time, not here. Returning None is
        # fine — engine treats None as "unknown, always re-process when emitted".
        return None

    async def sync(self, opts: SyncOptions) -> AsyncIterator[ObjectChange]:
        self.ctx.declare_enumeration("full")
        old = await self.state.get("objects") or {}
        seen: dict[str, str] = {}

        # Chats subtree: one message_stream per group chat the bot is a member of.
        for chat in await self._chats():
            p = f"/chats/{self._dir_name(chat)}/messages.jsonl"
            seen[p] = ""
            if opts.full or p not in old:
                yield ObjectChange(p, "added" if p not in old else "modified")
            # message_stream re-index every sync (incremental fetch handled by message.list pagination)
            elif old.get(p) != "":
                yield ObjectChange(p, "modified")

        # Docs subtree: one text document per accessible docx file. Fingerprint by
        # modified_time so an unchanged doc is skipped on incremental sync.
        for doc in await self._docs():
            p = f"/docs/{self._doc_name(doc)}"
            fp = doc.get("modified_time") or ""
            seen[p] = fp
            if opts.full or old.get(p) != fp:
                yield ObjectChange(p, "added" if p not in old else "modified")

        for p in set(old) - set(seen):
            yield ObjectChange(p, "deleted")
        await self.state.set("objects", seen)
