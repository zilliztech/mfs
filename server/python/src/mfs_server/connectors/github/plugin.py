"""GitHub connector — code tree + issues/PRs. httpx GitHub REST:
/repos/{o}/{r} -> default_branch; /git/trees/{br}?recursive=1 -> blobs; raw.github-
usercontent.com for content. Auth via GITHUB_TOKEN env (anonymous rate limit is low).

The code tree maps to file-like paths (object_kind reuses file's ext mapping). The
`_meta/` subtree exposes collaboration data:
  _meta/issues.jsonl          all issues (record_collection)
  _meta/pulls.jsonl           all pull requests (record_collection)
  _meta/pulls/<n>/diff.patch  per-PR unified diff (document)
Set config `index_meta=true` to enable issues/pulls (off by default — they can be
large and need text_fields config to be searchable).
"""

from __future__ import annotations

import mimetypes
import os
from collections.abc import AsyncIterator
from typing import Optional

import httpx

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

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"


class GitHubPlugin(ConnectorPlugin):
    NAME = "github"
    URI_SCHEME = "github"
    DISPLAY_NAME = "GitHub"
    PROMPT = "A GitHub repository's code tree (files at their repo paths)."
    CAPABILITIES = Capabilities(
        manual_sync=True,
        watch=False,
        cursor_kind="blob_sha",
        full_scan=True,
        delete_detection="full_scan",
        paged_cat=True,
    )

    def _cfg(self, key, default=None):
        return (
            self.config.get(key, default)
            if isinstance(self.config, dict)
            else getattr(self.config, key, default)
        )

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

    def preset_for(self, path: str):
        if path.endswith("issues.jsonl"):
            return "github.issues"
        if path.endswith("pulls.jsonl"):
            return "github.pulls"
        return None

    def object_kind_of(self, path: str) -> ObjectKind:
        if path.startswith("/_meta/"):
            if path.endswith(".jsonl"):
                return "record_collection"
            if path.endswith("diff.patch"):
                return "document"
            return "directory"
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

    def _index_meta(self) -> bool:
        # opt-in: issues/PRs can be huge and need text_fields config to be searchable,
        # so the default connector indexes only the code tree.
        return bool(self._cfg("index_meta", False))

    async def stat(self, path: str) -> PathStat:
        if path.startswith("/_meta"):
            if path.endswith(".jsonl"):
                return PathStat(
                    path=path, type="file", media_type="application/x-ndjson", extra={"lazy": True}
                )
            if path.endswith("diff.patch"):
                return PathStat(path=path, type="file", media_type="text/x-diff")
            return PathStat(path=path, type="dir")
        blobs = await self.state.get("blobs") or {}
        if path == "/" or path.endswith("/"):
            return PathStat(path=path, type="dir")
        return PathStat(
            path=path, type="file", media_type=self._media_type(path), fingerprint=blobs.get(path)
        )

    async def _paginated(self, client: httpx.AsyncClient, url: str) -> list[dict]:
        """GET a list endpoint, following per_page pages until a short page or the cap."""
        cap = int(self._cfg("max_read_rows", 5000))
        out, page = [], 1
        while len(out) < cap:
            resp = await client.get(
                url, params={"state": "all", "per_page": 100, "page": page}, timeout=30
            )
            if resp.status_code != 200:
                break
            batch = resp.json()
            if not isinstance(batch, list) or not batch:
                break
            out.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return out[:cap]

    @staticmethod
    def _flatten_issue(it: dict) -> dict:
        return {
            "number": it.get("number"),
            "title": it.get("title"),
            "body": it.get("body"),
            "state": it.get("state"),
            "user": (it.get("user") or {}).get("login"),
            "labels": [lb.get("name") for lb in it.get("labels", [])],
            "created_at": it.get("created_at"),
            "updated_at": it.get("updated_at"),
        }

    @staticmethod
    def _flatten_pull(p: dict) -> dict:
        return {
            "number": p.get("number"),
            "title": p.get("title"),
            "body": p.get("body"),
            "state": p.get("state"),
            "user": (p.get("user") or {}).get("login"),
            "created_at": p.get("created_at"),
            "merged_at": p.get("merged_at"),
        }

    async def list(self, path: str) -> list[Entry]:
        if path in ("", "/"):
            blobs = await self.state.get("blobs") or {}
            top: dict[str, str] = {}
            for p in blobs:
                seg = p.lstrip("/").split("/", 1)
                top[seg[0]] = "file" if len(seg) == 1 else "dir"
            if self._index_meta():
                top["_meta"] = "dir"
            return [
                Entry(n, t, self._media_type(n) if t == "file" else None)
                for n, t in sorted(top.items())
            ]
        if path.rstrip("/") == "/_meta":
            return [
                Entry("issues.jsonl", "file", "application/x-ndjson", extra={"lazy": True}),
                Entry("pulls.jsonl", "file", "application/x-ndjson", extra={"lazy": True}),
                Entry("pulls", "dir"),
            ]
        blobs = await self.state.get("blobs") or {}
        prefix = path.rstrip("/") + "/"
        seen: dict[str, str] = {}
        for p in blobs:
            if p.startswith(prefix):
                rest = p[len(prefix) :]
                parts = rest.split("/", 1)
                seen[parts[0]] = "file" if len(parts) == 1 else "dir"
        return [
            Entry(name=n, type=t, media_type=self._media_type(n) if t == "file" else None)
            for n, t in sorted(seen.items())
        ]

    async def read(self, path: str, range: Optional[Range] = None) -> AsyncIterator[bytes]:
        o, r = self._owner_repo()
        if path.startswith("/_meta/pulls/") and path.endswith("/diff.patch"):
            num = path[len("/_meta/pulls/") : -len("/diff.patch")]
            headers = {**self._headers(), "Accept": "application/vnd.github.diff"}
            async with httpx.AsyncClient(headers=headers) as c:
                resp = await c.get(f"{API}/repos/{o}/{r}/pulls/{num}", timeout=30)
                yield resp.content
            return
        if path.startswith("/_meta/"):
            async for chunk in super().read(path, range):  # jsonl via read_records
                yield chunk
            return
        br = await self.state.get("branch") or self._cfg("branch") or "main"
        url = f"{RAW}/{o}/{r}/{br}{path}"  # path has leading '/'
        async with httpx.AsyncClient(headers=self._headers()) as c:
            resp = await c.get(url, timeout=30)
            yield resp.content

    def read_records(self, path: str, range: Optional[Range] = None):
        o, r = self._owner_repo()
        cap = int(self._cfg("max_read_rows", 5000))
        if path == "/_meta/issues.jsonl":

            async def issues():
                async with httpx.AsyncClient(headers=self._headers()) as c:
                    items = await self._paginated(c, f"{API}/repos/{o}/{r}/issues")
                    if len(items) >= cap:
                        self.ctx.declare_partial(path)  # hit cap -> partial recall
                    for it in items:
                        if "pull_request" in it:  # the issues API also returns PRs
                            continue
                        yield self._flatten_issue(it)

            return issues()
        if path == "/_meta/pulls.jsonl":

            async def pulls():
                async with httpx.AsyncClient(headers=self._headers()) as c:
                    items = await self._paginated(c, f"{API}/repos/{o}/{r}/pulls")
                    if len(items) >= cap:
                        self.ctx.declare_partial(path)
                    for p in items:
                        yield self._flatten_pull(p)

            return pulls()
        return None

    async def fingerprint(self, path: str) -> Optional[str]:
        blobs = await self.state.get("blobs") or {}
        return blobs.get(path)

    async def sync(self, opts: SyncOptions) -> AsyncIterator[ObjectChange]:
        self.ctx.declare_enumeration("full")
        o, r = self._owner_repo()
        async with httpx.AsyncClient(headers=self._headers()) as c:
            br = await self._branch(c)
            await self.state.set("branch", br)
            tree = (
                await c.get(f"{API}/repos/{o}/{r}/git/trees/{br}?recursive=1", timeout=30)
            ).json()
            old = await self.state.get("blobs") or {}
            blobs = {"/" + x["path"]: x["sha"] for x in tree.get("tree", []) if x["type"] == "blob"}
            for p, sha in blobs.items():
                if not opts.full and old.get(p) == sha:
                    continue
                yield ObjectChange(uri=p, kind="modified" if p in old else "added")
            for p in set(old) - set(blobs):
                yield ObjectChange(uri=p, kind="deleted")
            await self.state.set("blobs", blobs)

            if self._index_meta():
                yield ObjectChange(uri="/_meta/issues.jsonl", kind="modified")
                yield ObjectChange(uri="/_meta/pulls.jsonl", kind="modified")
                # one diff.patch document per (open+closed) PR
                pulls = await self._paginated(c, f"{API}/repos/{o}/{r}/pulls")
                for p in pulls:
                    yield ObjectChange(
                        uri=f"/_meta/pulls/{p['number']}/diff.patch", kind="modified"
                    )
