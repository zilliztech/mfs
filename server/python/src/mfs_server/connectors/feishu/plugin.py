"""Feishu / Lark connector — group chats as message_stream + docs as text.

Two subtrees in one connector:
  /chats/<name>__<chat-id>/messages.jsonl   group messages (lazy stream)
  /docs/<title>__<doc-token>.md             docx document body (rendered text)

Two auth modes (selected by `auth = "tenant" | "user"` in the connector config):

  * tenant (default) — bot identity, app_id + app_secret. Covers only chats
    the bot is a member of + docs the bot is a collaborator on. p2p single
    chats are NOT enumerable via REST under tenant auth (documented Feishu
    limit on chat.list).

  * user — OAuth Device Flow user identity. Covers everything the human user
    sees. `oauth_state_file` (NOT credential_ref — Feishu's refresh_token is
    one-shot, rotated every refresh, so the plugin must own read/write of the
    file) points at an oauth.json blob produced by
    `python -m mfs_server.connectors.feishu.auth_login`. The plugin refreshes
    the access_token on every connect, atomically writes the rotated
    refresh_token back, and every API call carries
    `RequestOption.user_access_token(...)` so the SDK acts as the user.

API endpoints used (all sync, wrapped in asyncio.to_thread):
  im.v1.chat.list                    -> caller's group chats
  im.v1.message.list                 -> messages in one chat
  drive.v1.file.list                 -> docs/sheets/etc. visible to caller
  docx.v1.document.raw_content       -> plain-text body of a docx document
  docx.v1.document.get               -> document metadata (title, revision_id)
"""

from __future__ import annotations

import asyncio
import datetime
import json
import re
from collections.abc import AsyncIterator
from typing import Optional

import httpx
import lark_oapi as lark
from lark_oapi.api.docx.v1 import GetDocumentRequest, RawContentDocumentRequest
from lark_oapi.api.drive.v1 import ListFileRequest
from lark_oapi.api.im.v1 import ListChatRequest, ListMessageRequest

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

# Refresh the user access_token this many seconds before it actually expires, so a call
# made right at the edge doesn't race the expiry.
_ACCESS_TOKEN_SKEW_S = 120

# Feishu codes meaning the user_access_token is expired / invalid. On one of these in user
# mode we refresh once and retry — covers a long job outliving the ~2h access_token.
_TOKEN_EXPIRED_CODES = frozenset({99991663, 99991664, 99991668, 99991677})


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
    if msg_type == "post":  # rich text
        out = []
        for block in data.get("content") or []:
            for el in block:
                if isinstance(el, dict) and el.get("text"):
                    out.append(el["text"])
        return " ".join(out)
    return data.get("text") or content or ""


def _since_ts(since: str) -> float:
    """Parse a --since date/datetime to unix seconds (Feishu file modified_time is unix sec)."""
    s = since.strip()
    if "T" not in s:
        s = s + "T00:00:00"
    return datetime.datetime.fromisoformat(s).timestamp()


class FeishuPlugin(ConnectorPlugin):
    NAME = "feishu"
    URI_SCHEME = "feishu"
    DISPLAY_NAME = "Feishu / Lark"
    PROMPT = (
        "Feishu group chats as /chats/<name>__<id>/messages.jsonl + "
        "docx documents as /docs/<title>__<doc-token>.md."
    )
    CAPABILITIES = Capabilities(
        manual_sync=True,
        watch=False,
        cursor_kind="create_time",
        since_pushdown=True,
        full_scan=True,
        delete_detection="never",
        paged_cat=True,
    )

    def __init__(self, config, credential, *, ctx):
        super().__init__(config, credential, ctx=ctx)
        self._client = None
        self._user_token: Optional[str] = None  # set when auth = "user"
        self._access_expires_at: float = 0.0  # unix ts the user access_token expires (user mode)
        self._oauth_path = None  # Path to the oauth state file (user mode), reused on refresh
        self._app_id: Optional[str] = None  # cached from config / oauth.json blob
        self._app_secret: Optional[str] = None  # used for tenant_access_token mint
        self._region: str = "feishu"  # "feishu" (open.feishu.cn) | "lark" (open.larksuite.com)

    def _cfg(self, k, d=None):
        return (
            self.config.get(k, d) if isinstance(self.config, dict) else getattr(self.config, k, d)
        )

    def _opt(self):
        """Build a per-call option override. tenant mode -> None (SDK uses its
        own tenant_access_token derived from app_id+secret); user mode -> a
        RequestOption carrying the refreshed user_access_token so every API
        call acts as the human, not the bot."""
        if self._user_token:
            return lark.RequestOption.builder().user_access_token(self._user_token).build()
        return None

    @staticmethod
    def _sdk_domain(region: str):
        # lark-oapi ships FEISHU_DOMAIN / LARK_DOMAIN string constants we hand to
        # Client.builder().domain(); this maps our "feishu" / "lark" config value
        # to whichever the installed SDK exposes (constant names are stable).
        return lark.LARK_DOMAIN if region == "lark" else lark.FEISHU_DOMAIN

    async def connect(self) -> None:
        auth_mode = self._cfg("auth", "user")
        if auth_mode == "user":
            import json as _json
            import time as _time
            from pathlib import Path

            tok_path_cfg = self._cfg("oauth_state_file")
            if not tok_path_cfg:
                raise ValueError(
                    "feishu auth='user' requires `oauth_state_file` in the connector config. "
                    "Authorize with `mfs connector auth <connector-uri>`."
                )
            self._oauth_path = Path(tok_path_cfg).expanduser()
            try:
                blob = _json.loads(self._oauth_path.read_text())
            except (OSError, ValueError) as e:
                raise ValueError(
                    f"feishu oauth_state_file {self._oauth_path}: cannot load ({e}). "
                    "Authorize with `mfs connector auth <connector-uri>`."
                ) from e
            self._app_id, self._app_secret = blob.get("app_id"), blob.get("app_secret")
            if not (self._app_id and self._app_secret and blob.get("refresh_token")):
                raise ValueError(
                    f"feishu oauth_state_file {self._oauth_path}: missing app_id / app_secret / "
                    "refresh_token. Authorize with `mfs connector auth <connector-uri>`."
                )
            # Region: prefer the value persisted in the blob (the OAuth was performed against a
            # specific region; refresh must use the same one). Fall back to config, then feishu.
            self._region = blob.get("region") or self._cfg("region", "feishu")
            # Lazy: reuse a still-valid access_token; only refresh (which rotates the one-shot
            # refresh_token) when it's missing or near expiry. Keeping refreshes infrequent is
            # what lets concurrent reads share one token without fighting over the rotation.
            if blob.get("access_token") and (
                blob.get("access_expires_at", 0) - _time.time() > _ACCESS_TOKEN_SKEW_S
            ):
                self._user_token = blob["access_token"]
                self._access_expires_at = blob["access_expires_at"]
            else:
                await self._refresh_user_access(blob)
            dom = self._sdk_domain(self._region)
            app_id, app_secret = self._app_id, self._app_secret
            self._client = await asyncio.to_thread(
                lambda: (
                    lark.Client.builder().app_id(app_id).app_secret(app_secret).domain(dom).build()
                )
            )
            return

        # Default: tenant (bot) identity.
        self._region = self._cfg("region", "feishu")
        self._app_id = self._cfg("app_id")
        self._app_secret = self._cfg("app_secret") or self.credential
        dom = self._sdk_domain(self._region)

        def build():
            return (
                lark.Client.builder()
                .app_id(self._app_id)
                .app_secret(self._app_secret)
                .domain(dom)
                .build()
            )

        self._client = await asyncio.to_thread(build)

    async def _refresh_user_access(self, blob: dict) -> None:
        """Exchange the stored refresh_token for a fresh access_token, rotate the one-shot
        refresh_token, and persist both (atomic write). Raises a needs-reauth ValueError if
        the refresh_token is dead (expired / revoked)."""
        import json as _json
        import time as _time

        from .oauth import OAuthError, refresh_user_token

        try:
            tok = await asyncio.to_thread(
                refresh_user_token,
                self._app_id,
                self._app_secret,
                blob.get("refresh_token"),
                self._region,
            )
        except OAuthError as e:
            raise ValueError(
                f"feishu user authorization expired or revoked ({e}). Re-authorize with "
                "`mfs connector auth <connector-uri>`."
            ) from e
        now = int(_time.time())
        self._user_token = tok["access_token"]
        self._access_expires_at = now + tok.get("expires_in", 7200)
        new_blob = dict(blob)
        new_blob["refresh_token"] = tok["refresh_token"]
        new_blob["access_token"] = tok["access_token"]
        new_blob["access_expires_at"] = self._access_expires_at
        new_blob["obtained_at"] = now
        new_blob["region"] = self._region
        tmp = self._oauth_path.with_suffix(self._oauth_path.suffix + ".tmp")
        tmp.write_text(_json.dumps(new_blob, ensure_ascii=False, indent=2))
        tmp.chmod(0o600)
        tmp.replace(self._oauth_path)

    async def _call(self, fn, ctx: str):
        """Run a lark SDK call (sync `fn` returning a resp). In user mode, if the call fails
        with an expired-token code, refresh once and retry (covers a long job outliving the
        access_token); a uniform RuntimeError is raised if it still fails."""
        resp = await asyncio.to_thread(fn)
        if (
            not resp.success()
            and self._user_token
            and self._oauth_path is not None
            and resp.code in _TOKEN_EXPIRED_CODES
        ):
            import json as _json

            await self._refresh_user_access(_json.loads(self._oauth_path.read_text()))
            resp = await asyncio.to_thread(fn)
        if not resp.success():
            raise RuntimeError(f"feishu {ctx} failed: code={resp.code} msg={resp.msg}")
        return resp

    async def healthcheck(self) -> HealthStatus:
        try:
            await self._chats()
            return HealthStatus(ok=True)
        except Exception as e:  # noqa: BLE001
            return HealthStatus(ok=False, detail=str(e))

    def _parts(self, path: str) -> list[str]:
        return [p for p in path.strip("/").split("/") if p]

    def _access_token(self) -> str:
        """Return the right access_token for the current auth mode. Used by raw httpx
        calls to endpoints lark-oapi doesn't wrap (e.g. chat_p2p/batch_query)."""
        if self._user_token:
            return self._user_token
        # tenant mode — mint a tenant_access_token via /auth/v3/internal
        from .oauth import endpoints

        r = httpx.post(
            endpoints(self._region)["open"] + "/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": self._app_id, "app_secret": self._app_secret},
            timeout=15,
        )
        return r.json()["tenant_access_token"]

    async def _resolve_p2p_chats(self, partner_open_ids: list[str]) -> list[dict]:
        """Reverse-look p2p chat_ids from partner open_ids. Returns
        [{chat_id, chatter_id1, chatter_id2}, ...]. Calls
        POST /open-apis/im/v1/chat_p2p/batch_query — endpoint not wrapped by the
        lark-oapi SDK, so we hit HTTP directly. Endpoint shape
        (chatter_id_type / chatter_ids / response p2p_chats[]) verified against
        larksuite-cli source (shortcuts/im/helpers.go).
        """
        if not partner_open_ids:
            return []
        token = await asyncio.to_thread(self._access_token)
        from .oauth import endpoints

        url = endpoints(self._region)["open"] + "/open-apis/im/v1/chat_p2p/batch_query"

        def fetch():
            return httpx.post(
                url,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                params={"chatter_id_type": "open_id"},
                json={"chatter_ids": list(partner_open_ids)},
                timeout=20,
            )

        r = await asyncio.to_thread(fetch)
        if r.status_code >= 400:
            raise RuntimeError(
                f"feishu chat_p2p/batch_query failed: {r.status_code} {r.text[:200]}"
            )
        body = r.json()
        if body.get("code") != 0:
            raise RuntimeError(
                f"feishu chat_p2p/batch_query: code={body.get('code')} msg={body.get('msg')}"
            )
        return [
            {
                "chat_id": p.get("chat_id"),
                "chatter_id1": p.get("chatter_id1"),
                "chatter_id2": p.get("chatter_id2"),
            }
            for p in (body.get("data", {}).get("p2p_chats") or [])
            if p.get("chat_id")
        ]

    async def _chats(self) -> list[dict]:
        """Enumerate chats from three sources, de-duped by chat_id:

        1. `chat.list` — caller's group chats (tenant mode: bot's groups; user mode:
           user's groups). Excludes p2p chats by Feishu's API design — even with user
           token and im:chat:readonly, p2p single chats are NEVER returned here.

        2. `extra_chats[].chat_id` — user-supplied literal chat_ids. The escape
           hatch for any chat the user already knows the chat_id of.

        3. `extra_chats[].partner_open_id` — auto-resolve p2p chat_ids by looking
           up the chat the caller has with each partner via
           `chat_p2p/batch_query`. Friendlier than asking the user to dig out
           `oc_xxx` chat_ids — they only need the partner's `ou_xxx` open_id (which
           is on every contact's Feishu profile, and the bot's own open_id is in the
           developer console).
        """

        def run():
            req = ListChatRequest.builder().build()
            return self._client.im.v1.chat.list(req, self._opt())

        resp = await self._call(run, "chat.list")
        chats = [{"chat_id": c.chat_id, "name": c.name} for c in (resp.data.items or [])]
        by_id: dict[str, dict] = {c["chat_id"]: c for c in chats}

        # Literal chat_ids — wins over chat.list (the user knows best)
        literal_partner_oids: list[str] = []
        partner_label: dict[str, str] = {}
        for ex in self._cfg("extra_chats") or []:
            if not isinstance(ex, dict):
                continue
            cid = ex.get("chat_id")
            if cid:
                by_id[cid] = {"chat_id": cid, "name": ex.get("label") or cid}
            elif ex.get("partner_open_id"):
                oid = ex["partner_open_id"]
                literal_partner_oids.append(oid)
                if ex.get("label"):
                    partner_label[oid] = ex["label"]

        # Auto-resolve partner open_ids -> chat_ids via chat_p2p/batch_query
        if literal_partner_oids:
            resolved = await self._resolve_p2p_chats(literal_partner_oids)
            requested = set(literal_partner_oids)
            for p in resolved:
                cid = p["chat_id"]
                # the partner is whichever chatter_id is NOT us
                partner = (
                    p["chatter_id1"]
                    if p["chatter_id1"] in requested
                    else (p["chatter_id2"] if p["chatter_id2"] in requested else None)
                )
                label = partner_label.get(partner) or partner or cid
                by_id[cid] = {"chat_id": cid, "name": label}

        return list(by_id.values())

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

    async def _list_folder_docx(self, folder_token: str | None) -> list[dict]:
        """Recursively enumerate docx documents under a Drive folder, or the caller's My
        Space root when folder_token is None (user mode). Recurses into subfolders."""
        docs: list[dict] = []
        subfolders: list[str] = []

        def fetch(pt):
            b = ListFileRequest.builder().page_size(100)
            if folder_token:
                b = b.folder_token(folder_token)
            if pt:
                b = b.page_token(pt)
            return self._client.drive.v1.file.list(b.build(), self._opt())

        page_token = None
        while True:
            data = (
                await self._call(
                    lambda pt=page_token: fetch(pt),
                    f"drive.file.list(folder={folder_token or 'root'})",
                )
            ).data
            for f in data.files or []:
                t = getattr(f, "type", None)
                if t == "docx":
                    # only docx for now — sheets / bitable / mindnote etc. have very
                    # different read APIs and aren't usefully searchable as plain text.
                    docs.append(
                        {
                            "token": f.token,
                            "name": getattr(f, "name", None) or f.token,
                            "modified_time": getattr(f, "modified_time", None),
                        }
                    )
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

    async def _docs(self, since_ts: float | None = None) -> list[dict]:
        """Discover docx documents to index:

        1. `docs_folder_token` — recursively enumerate docs under one shared folder.
        2. user mode with no folder/extra set — recursively enumerate the caller's My
           Space root (the documents the user owns).
        3. `extra_docs` (list[{token, label}]) — explicit per-doc list, always included.

        When `since_ts` is set, discovered docs older than it (by modified_time) are
        skipped; explicitly-named `extra_docs` are always kept. De-duped by `token`;
        `extra_docs` wins on name conflicts."""
        out: dict[str, dict] = {}

        # discover: a shared folder, else (user mode, no explicit scope) the My Space root
        folder_token = self._cfg("docs_folder_token") or ""
        extras = self._cfg("extra_docs") or []
        discovered: list[dict] = []
        if folder_token:
            discovered = await self._list_folder_docx(folder_token)
        elif self._cfg("auth", "user") == "user" and not extras:
            discovered = await self._list_folder_docx(None)
        for d in discovered:
            if since_ts is not None:
                mt = d.get("modified_time")
                try:
                    if mt is None or float(mt) < since_ts:
                        continue
                except (TypeError, ValueError):
                    pass  # unparseable modified_time -> keep rather than silently drop
            out[d["token"]] = d

        # explicit extra_docs (always included; not since-filtered — the user named them)
        for extra in extras:
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
            return self._client.docx.v1.document.raw_content(req, self._opt())

        resp = await self._call(fetch, f"docx.raw_content({doc_id})")
        return getattr(resp.data, "content", "") or ""

    async def _doc_meta(self, doc_id: str) -> dict:
        """Document metadata (title + revision_id) for fingerprinting."""

        def fetch():
            req = GetDocumentRequest.builder().document_id(doc_id).build()
            return self._client.docx.v1.document.get(req, self._opt())

        resp = await self._call(fetch, f"docx.get({doc_id})")
        d = resp.data.document if resp.data else None
        return {
            "title": getattr(d, "title", None) if d else None,
            "revision_id": getattr(d, "revision_id", None) if d else None,
        }

    def preset_for(self, path: str):
        # auto-apply the feishu.messages PRESET (text_fields=["text"], group_by=
        # "thread_id", etc.) so users don't have to spell out [[objects]] for chats.
        return "feishu.messages" if path.endswith("messages.jsonl") else None

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
            return PathStat(
                path=path, type="file", media_type="application/x-ndjson", extra={"lazy": True}
            )
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
                return self._client.im.v1.message.list(b.build(), self._opt())

            while n < limit:
                data = (await self._call(lambda pt=page_token: fetch(pt), "message.list")).data
                for it in data.items or []:
                    msg_type = it.msg_type
                    content = it.body.content if it.body else ""
                    yield {
                        "message_id": it.message_id,
                        "msg_type": msg_type,
                        "create_time": it.create_time,
                        "sender": getattr(it.sender, "id", None) if it.sender else None,
                        "thread_id": getattr(it, "thread_id", None) or getattr(it, "root_id", None),
                        "text": _extract_text(msg_type, content),
                    }
                    n += 1
                    if n >= limit:
                        break  # honour max_read_rows mid-page
                if n >= limit:
                    break
                if not data.has_more:
                    break
                page_token = data.page_token

    async def fingerprint(self, path: str) -> Optional[str]:
        # Docs: modified_time from Drive listing already in sync's `seen` map; the
        # actual revision check happens at sync time, not here. Returning None is
        # fine — engine treats None as "unknown, always re-process when emitted".
        return None

    async def sync(self, opts: SyncOptions) -> AsyncIterator[ObjectChange]:
        since_ts = _since_ts(opts.since) if opts.since else None
        # since => docs were narrowed to recent ones; don't treat the rest as deleted.
        self.ctx.declare_enumeration("incremental" if since_ts else "full")
        old = await self.state.get("objects") or {}
        seen: dict[str, str] = {}

        # Chats subtree: one message_stream per group chat the user/bot is a member of.
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
        for doc in await self._docs(since_ts):
            p = f"/docs/{self._doc_name(doc)}"
            fp = doc.get("modified_time") or ""
            seen[p] = fp
            if opts.full or old.get(p) != fp:
                yield ObjectChange(p, "added" if p not in old else "modified")

        if since_ts is None:
            for p in set(old) - set(seen):
                yield ObjectChange(p, "deleted")
        # since: merge so docs outside the window stay tracked (not re-indexed); full: replace
        await self.state.set("objects", {**old, **seen} if since_ts else seen)
