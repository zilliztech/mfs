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

    # Rendered page markdown, shared PROCESS-WIDE across plugin instances, keyed by
    # (connector_id, path). The in-process worker runs the index pass on a FRESH plugin
    # instance, and this job's connector state is staged (engine commits state_snapshot
    # only after the job succeeds), so both a per-instance cache and a state lookup are
    # empty at index time -> read() returns "" -> 0 chunks / empty cat. A process-wide
    # cache bridges the sync instance and the worker instance within the same run; the
    # markdown is ALSO persisted in `state` (see sync) so it survives a process restart.
    # Bounded by max_pages (default 50) per connector.
    _MD_CACHE: dict[tuple[str, str], str] = {}

    def __init__(self, config, credential, *, ctx):
        super().__init__(config, credential, ctx=ctx)

    def _cache_get(self, path: str) -> Optional[str]:
        return self._MD_CACHE.get((self.ctx.connector_id, path))

    def _cache_put(self, path: str, md: str) -> None:
        self._MD_CACHE[(self.ctx.connector_id, path)] = md

    def _cache_paths(self) -> set[str]:
        cid = self.ctx.connector_id
        return {p for (c, p) in self._MD_CACHE if c == cid}

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
        # Resolve via the process-wide cache, then durable state, so size_hint is correct
        # on a fresh instance (worker index pass / post-restart), not only within sync.
        md = self._cache_get(path)
        if not md:
            md = await self._md_from_state(path)
        return PathStat(
            path=path,
            type="file" if path.endswith(".md") else "dir",
            media_type="text/markdown",
            size_hint=len(md) if md else None,
        )

    async def list(self, path: str) -> list[Entry]:
        prefix = path.rstrip("/") + "/"
        # Enumerate page paths from the process cache AND durable state, so `ls`/`tree`
        # work both within a sync and on a fresh instance (post-index / post-restart).
        pages = await self.state.get("pages") or {}
        seen: dict[str, str] = {}
        for p in self._cache_paths() | set(pages):
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

    async def _md_from_state(self, path: str) -> str:
        """Load a page's rendered markdown from durable connector state. Used on the
        cold path — after a process restart the process-wide cache is empty, so content
        must come from state (committed once the syncing job succeeded)."""
        pages = await self.state.get("pages") or {}
        entry = pages.get(path)
        return entry.get("md", "") if isinstance(entry, dict) else ""

    async def read(self, path: str, range: Optional[Range] = None) -> AsyncIterator[bytes]:
        # Warm path: process-wide cache (bridges the sync instance and the worker's
        # index pass in the same run). Cold path: the markdown persisted in state
        # (post-restart cat/search). Only a genuinely-unknown path yields "".
        md = self._cache_get(path)
        if not md:
            md = await self._md_from_state(path)
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
                self._cache_put(path, md)
                kind = "modified" if path in old_pages else "added"
                links = self._extract_links(html, url)
                # Persist the etag (for next 304), the link list (so the 304 path
                # above can still drive BFS), AND the rendered markdown. State is the
                # durable copy that survives a restart; without it a post-restart
                # read() would return "" (empty cat / unsearchable page) once the
                # process cache is gone. Bounded by max_pages (default 50).
                pages[path] = {"etag": etag, "links": links, "md": md}
                yield ObjectChange(uri=path, kind=kind)
                for link in links:
                    if link not in visited:
                        queue.append(link)
                crawled += 1
        await self.state.set("pages", pages)
