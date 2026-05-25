"""GitHub connector — public repo code tree (design/09 GitHub). httpx GitHub REST:
/repos/{o}/{r} -> default_branch; /git/trees/{br}?recursive=1 -> blobs; raw.github-
usercontent.com for content. Auth via GITHUB_TOKEN env (anonymous rate limit is low).
Phase 6: code tree only (issues/pulls later). object_kind reuses file's ext mapping.
"""
from __future__ import annotations

import mimetypes
import os
from collections.abc import AsyncIterator
from typing import Optional

import httpx

from ..base import Capabilities, ConnectorPlugin, Entry, ObjectChange, ObjectKind, PathStat, Range, SyncOptions
from ..file.plugin import CODE_EXT, DOC_EXT, IMAGE_EXT, TEXTBLOB_EXT

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"


class GitHubPlugin(ConnectorPlugin):
    NAME = "github"
    URI_SCHEME = "github"
    DISPLAY_NAME = "GitHub"
    PROMPT = "A GitHub repository's code tree (files at their repo paths)."
    CAPABILITIES = Capabilities(manual_sync=True, watch=False, cursor_kind="blob_sha",
                                full_scan=True, delete_detection="full_scan", paged_cat=True)

    def _cfg(self, key, default=None):
        return self.config.get(key, default) if isinstance(self.config, dict) else getattr(self.config, key, default)

    def _owner_repo(self) -> tuple[str, str]:
        o, r = self._cfg("repo").split("/", 1)
        return o, r

    def _headers(self) -> dict:
        t = os.environ.get("GITHUB_TOKEN")
        h = {"User-Agent": "mfs-github/0.4", "Accept": "application/vnd.github+json"}
        if t:
            h["Authorization"] = f"Bearer {t}"
        return h

    async def _branch(self, client: httpx.AsyncClient) -> str:
        b = self._cfg("branch")
        if b:
            return b
        o, r = self._owner_repo()
        resp = await client.get(f"{API}/repos/{o}/{r}")
        return resp.json()["default_branch"]

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
        blobs = await self.state.get("blobs") or {}
        if path == "/" or path.endswith("/"):
            return PathStat(path=path, type="dir")
        return PathStat(path=path, type="file", media_type=self._media_type(path),
                        fingerprint=blobs.get(path))

    async def list(self, path: str) -> list[Entry]:
        blobs = await self.state.get("blobs") or {}
        prefix = "/" if path in ("", "/") else path.rstrip("/") + "/"
        seen: dict[str, str] = {}
        for p in blobs:
            if p.startswith(prefix):
                rest = p[len(prefix):]
                parts = rest.split("/", 1)
                seen[parts[0]] = "file" if len(parts) == 1 else "dir"
        return [Entry(name=n, type=t, media_type=self._media_type(n) if t == "file" else None)
                for n, t in sorted(seen.items())]

    async def read(self, path: str, range: Optional[Range] = None) -> AsyncIterator[bytes]:
        o, r = self._owner_repo()
        br = await self.state.get("branch") or self._cfg("branch") or "main"
        url = f"{RAW}/{o}/{r}/{br}{path}"      # path has leading '/'
        async with httpx.AsyncClient(headers=self._headers()) as c:
            resp = await c.get(url, timeout=30)
            yield resp.content

    async def fingerprint(self, path: str) -> Optional[str]:
        blobs = await self.state.get("blobs") or {}
        return blobs.get(path)

    async def sync(self, opts: SyncOptions) -> AsyncIterator[ObjectChange]:
        self.ctx.declare_enumeration("full")
        o, r = self._owner_repo()
        async with httpx.AsyncClient(headers=self._headers()) as c:
            br = await self._branch(c)
            await self.state.set("branch", br)
            tree = (await c.get(f"{API}/repos/{o}/{r}/git/trees/{br}?recursive=1", timeout=30)).json()
            old = await self.state.get("blobs") or {}
            blobs = {"/" + x["path"]: x["sha"] for x in tree.get("tree", []) if x["type"] == "blob"}
            for p, sha in blobs.items():
                if not opts.full and old.get(p) == sha:
                    continue
                yield ObjectChange(uri=p, kind="modified" if p in old else "added")
            for p in set(old) - set(blobs):
                yield ObjectChange(uri=p, kind="deleted")
            await self.state.set("blobs", blobs)
