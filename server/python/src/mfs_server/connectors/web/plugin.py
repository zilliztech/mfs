"""Web connector — static backend. aiohttp fetch + markitdown
HTML->md INLINE (backend-coupled, NOT the framework converter) + ETag/304 revisit.
URL->path canonicalization. Page md is cached on the
plugin instance during sync so the same-instance index pass can read it; the engine
also persists it as a converted_md artifact so later `cat` works without re-fetching.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator
from typing import Optional
from urllib.parse import urljoin, urlparse

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


class WebPlugin(ConnectorPlugin):
    NAME = "web"
    URI_SCHEME = "web"
    DISPLAY_NAME = "Web"
    PROMPT = "Crawled web pages converted to markdown under pages/<host>/<path>.md"
    CAPABILITIES = Capabilities(
        manual_sync=True,
        watch=False,
        cursor_kind="etag",
        full_scan=True,
        delete_detection="explicit",
        paged_cat=True,
    )

    def __init__(self, config, credential, *, ctx):
        super().__init__(config, credential, ctx=ctx)
        self._md_cache: dict[str, str] = {}

    def _cfg(self, key, default=None):
        return (
            self.config.get(key, default)
            if isinstance(self.config, dict)
            else getattr(self.config, key, default)
        )

    @staticmethod
    def url_to_path(url: str) -> str:
        u = urlparse(url)
        path = u.path.strip("/")
        if not path:
            path = "index"
        if path.endswith("/"):
            path = path.rstrip("/")
        return f"/pages/{u.netloc.lower()}/{path}.md"

    def _allowed(self, url: str) -> bool:
        domains = self._cfg("allowed_domains", []) or []
        return urlparse(url).netloc in domains if domains else True

    def object_kind_of(self, path: str) -> ObjectKind:
        return "document" if path.endswith(".md") else "directory"

    async def stat(self, path: str) -> PathStat:
        md = self._md_cache.get(path)
        return PathStat(
            path=path,
            type="file" if path.endswith(".md") else "dir",
            media_type="text/markdown",
            size_hint=len(md) if md else None,
        )

    async def list(self, path: str) -> list[Entry]:
        prefix = path.rstrip("/") + "/"
        seen: dict[str, str] = {}
        for p in self._md_cache:
            if p.startswith(prefix):
                rest = p[len(prefix) :]
                head = rest.split("/", 1)
                if len(head) == 1:
                    seen[head[0]] = "file"
                else:
                    seen[head[0]] = "dir"
        return [
            Entry(name=n, type=t, media_type="text/markdown" if t == "file" else None)
            for n, t in sorted(seen.items())
        ]

    async def read(self, path: str, range: Optional[Range] = None) -> AsyncIterator[bytes]:
        md = self._md_cache.get(path, "")
        yield md.encode()

    async def fingerprint(self, path: str) -> Optional[str]:
        pages = await self.state.get("pages") or {}
        entry = pages.get(path)
        # State schema migrated from `{path: etag_str}` (v0.4 early) to
        # `{path: {"etag": ..., "links": [...]}}` so we can re-walk the BFS
        # past a 304 without losing children. Old entries still parse cleanly.
        if isinstance(entry, dict):
            return entry.get("etag")
        return entry

    def _html_to_md(self, html: str) -> str:
        from markitdown import MarkItDown

        with tempfile.NamedTemporaryFile(
            suffix=".html", delete=False, mode="w", encoding="utf-8"
        ) as f:
            f.write(html)
            p = f.name
        try:
            return MarkItDown().convert(p).text_content
        finally:
            os.remove(p)

    def _extract_links(self, html: str, base: str) -> list[str]:
        from bs4 import BeautifulSoup

        out = []
        for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
            u = urljoin(base, a["href"]).split("#")[0]
            if u.startswith("http"):
                out.append(u)
        return out

    @staticmethod
    def _entry(prev) -> tuple[Optional[str], list[str]]:
        """Unpack a state.pages entry, tolerating the legacy `etag-string` form
        alongside the current `{etag, links}` dict form."""
        if isinstance(prev, dict):
            return prev.get("etag"), list(prev.get("links") or [])
        if isinstance(prev, str):
            return prev, []  # legacy: etag known, links lost
        return None, []

    async def sync(self, opts: SyncOptions) -> AsyncIterator[ObjectChange]:
        import aiohttp

        self.ctx.declare_enumeration("full")
        old_pages = await self.state.get("pages") or {}
        pages = dict(old_pages)
        start = list(self._cfg("start_urls", []) or [])
        max_pages = int(self._cfg("max_pages", 50))
        visited: set[str] = set()
        queue = list(start)
        crawled = 0
        async with aiohttp.ClientSession(headers={"User-Agent": "mfs-web/0.4"}) as sess:
            while queue and crawled < max_pages:
                url = queue.pop(0)
                if url in visited or not self._allowed(url):
                    continue
                visited.add(url)
                path = self.url_to_path(url)
                prev_etag, prev_links = self._entry(old_pages.get(path))
                headers = {}
                if prev_etag:
                    headers["If-None-Match"] = prev_etag
                try:
                    async with sess.get(
                        url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)
                    ) as resp:
                        if resp.status == 304:
                            # Content unchanged — but BFS must continue: re-enqueue
                            # the children we discovered last time, otherwise a
                            # mutation deeper in the tree would never be re-fetched
                            # this run. (Bug fix: previously this `continue` threw
                            # away link discovery for the page's subtree.)
                            for link in prev_links:
                                if link not in visited:
                                    queue.append(link)
                            crawled += 1
                            continue
                        if resp.status != 200:
                            continue
                        html = await resp.text()
                        etag = resp.headers.get("ETag", "")
                except Exception:  # noqa: BLE001
                    continue
                md = self._html_to_md(html)
                self._md_cache[path] = md
                kind = "modified" if path in old_pages else "added"
                links = self._extract_links(html, url)
                # Persist both the etag (for next 304) AND the link list (so the
                # 304 path above can still drive BFS).
                pages[path] = {"etag": etag, "links": links}
                yield ObjectChange(uri=path, kind=kind)
                for link in links:
                    if link not in visited:
                        queue.append(link)
                crawled += 1
        await self.state.set("pages", pages)
