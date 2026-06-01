"""Google Drive connector — mirrors the Drive file tree.
google-api-python-client (sync; wrapped in asyncio.to_thread). Native Google types
(Docs/Sheets/Slides) are exported (Docs->text, Sheets->CSV); regular files keep their
name/extension and are streamed via get_media.

API verified against google-api-python-client Drive v3 docs: build('drive','v3',
credentials); files().list(q, fields='nextPageToken, files(id,name,mimeType,parents,
modifiedTime,md5Checksum)', pageToken); files().get_media(fileId); files().export_media(
fileId, mimeType). NOT end-to-end tested (needs OAuth creds).
"""

from __future__ import annotations

import asyncio
import io
import mimetypes
import os
from collections.abc import AsyncIterator
from typing import Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from ..base import (
    Capabilities,
    ConnectorPlugin,
    Entry,
    ObjectChange,
    ObjectKind,
    PathStat,
    Range,
    SyncOptions,
)
from ..file.plugin import CODE_EXT, DOC_EXT, IMAGE_EXT, TEXTBLOB_EXT

# Google-native mime types -> (export mime, filename suffix, object_kind)
_NATIVE = {
    "application/vnd.google-apps.document": ("text/plain", ".txt", "document"),
    "application/vnd.google-apps.spreadsheet": ("text/csv", ".csv", "text_blob"),
    "application/vnd.google-apps.presentation": ("text/plain", ".txt", "document"),
}
_FOLDER = "application/vnd.google-apps.folder"


class GDrivePlugin(ConnectorPlugin):
    NAME = "gdrive"
    URI_SCHEME = "gdrive"
    DISPLAY_NAME = "Google Drive"
    PROMPT = "A Google Drive file tree (Docs exported to text, files at their paths)."
    CAPABILITIES = Capabilities(
        manual_sync=True,
        watch=False,
        cursor_kind="modifiedTime",
        full_scan=True,
        delete_detection="full_scan",
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
            creds = (
                Credentials.from_authorized_user_info(tok)
                if isinstance(tok, dict)
                else Credentials(token=tok)
            )
            return build("drive", "v3", credentials=creds, cache_discovery=False)

        self._svc = await asyncio.to_thread(build_svc)

    def _parts(self, path: str) -> list[str]:
        return [p for p in path.strip("/").split("/") if p]

    async def _meta(self) -> dict:
        """path -> {id, mimeType, fingerprint}; built during sync, cached in state."""
        return await self.state.get("files") or {}

    def object_kind_of(self, path: str) -> ObjectKind:
        ext = os.path.splitext(path)[1].lower()
        if ext in CODE_EXT:
            return "code"
        if ext in DOC_EXT:
            return "document"
        if ext in IMAGE_EXT:
            return "image"
        if ext in TEXTBLOB_EXT:
            return "text_blob"
        return "binary"

    def _media_type(self, path: str) -> Optional[str]:
        if path.endswith(".md"):
            return "text/markdown"
        mt, _ = mimetypes.guess_type(path)
        return mt

    async def stat(self, path: str) -> PathStat:
        if path == "/" or path.endswith("/"):
            return PathStat(path=path, type="dir")
        meta = await self._meta()
        info = meta.get(path)
        return PathStat(
            path=path,
            type="file",
            media_type=self._media_type(path),
            fingerprint=(info or {}).get("fingerprint"),
        )

    async def list(self, path: str) -> list[Entry]:
        meta = await self._meta()
        prefix = "/" if path in ("", "/") else path.rstrip("/") + "/"
        seen: dict[str, str] = {}
        for p in meta:
            if p.startswith(prefix):
                rest = p[len(prefix) :]
                seg = rest.split("/", 1)
                seen[seg[0]] = "file" if len(seg) == 1 else "dir"
        return [
            Entry(name=n, type=t, media_type=self._media_type(n) if t == "file" else None)
            for n, t in sorted(seen.items())
        ]

    async def read(self, path: str, range: Optional[Range] = None) -> AsyncIterator[bytes]:
        meta = await self._meta()
        info = meta.get(path)
        if not info:
            return
        fid, mime = info["id"], info["mimeType"]

        def download() -> bytes:
            if mime in _NATIVE:
                req = self._svc.files().export_media(fileId=fid, mimeType=_NATIVE[mime][0])
            else:
                req = self._svc.files().get_media(fileId=fid)
            buf = io.BytesIO()
            dl = MediaIoBaseDownload(buf, req)
            done = False
            while not done:
                _, done = dl.next_chunk()
            return buf.getvalue()

        yield await asyncio.to_thread(download)

    async def fingerprint(self, path: str) -> Optional[str]:
        meta = await self._meta()
        return (meta.get(path) or {}).get("fingerprint")

    async def _walk(self) -> dict[str, dict]:
        """List all files, resolve parent chain to a path. Native docs get a synthetic
        extension so object_kind_of routes them correctly."""

        def fetch_all():
            files, token = [], None
            q = "trashed = false"
            while True:
                resp = (
                    self._svc.files()
                    .list(
                        q=q,
                        pageToken=token,
                        pageSize=1000,
                        corpora="user",
                        fields="nextPageToken, files(id,name,mimeType,parents,modifiedTime,md5Checksum)",
                    )
                    .execute()
                )
                files.extend(resp.get("files", []))
                token = resp.get("nextPageToken")
                if not token:
                    break
            return files

        files = await asyncio.to_thread(fetch_all)
        by_id = {f["id"]: f for f in files}

        def path_of(f: dict) -> str:
            segs = [f["name"]]
            cur = f
            guard = 0
            while cur.get("parents") and guard < 64:
                parent = by_id.get(cur["parents"][0])
                if not parent:
                    break
                segs.append(parent["name"])
                cur = parent
                guard += 1
            return "/" + "/".join(reversed(segs))

        meta: dict[str, dict] = {}
        for f in files:
            if f["mimeType"] == _FOLDER:
                continue
            p = path_of(f)
            if f["mimeType"] in _NATIVE and not p.endswith(_NATIVE[f["mimeType"]][1]):
                p += _NATIVE[f["mimeType"]][1]
            meta[p] = {
                "id": f["id"],
                "mimeType": f["mimeType"],
                "fingerprint": f.get("md5Checksum") or f.get("modifiedTime"),
            }
        return meta

    async def sync(self, opts: SyncOptions) -> AsyncIterator[ObjectChange]:
        self.ctx.declare_enumeration("full")
        old = await self._meta()
        meta = await self._walk()
        await self.state.set("files", meta)
        for p, info in meta.items():
            if opts.full or (old.get(p) or {}).get("fingerprint") != info["fingerprint"]:
                yield ObjectChange(p, "modified" if p in old else "added")
        for p in set(old) - set(meta):
            yield ObjectChange(p, "deleted")
