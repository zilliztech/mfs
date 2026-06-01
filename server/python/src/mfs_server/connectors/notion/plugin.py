"""Notion connector — pages as document (.md),
data sources (the row-bearing entity formerly known as "database") as record_collection.
notion-client AsyncClient. Layout /pages/<id>.md (rendered block text) +
/data_sources/<id>/{schema.json,records.jsonl}.

API: AsyncClient(auth=token); client.search(filter={property:'object', value:'page'|'data_source'});
client.data_sources.query(data_source_id, start_cursor); client.blocks.children.list(...).
The 'database' filter value Notion accepted previously is now rejected — see Notion's
2025 database/data_source split (a database can hold N data_sources; each data_source is
the row collection).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Optional

from notion_client import AsyncClient

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


def _rich_text(rt: list) -> str:
    return "".join(t.get("plain_text", "") for t in (rt or []))


def _block_to_md(block: dict) -> str:
    """Render a single Notion block to a markdown line (common block types)."""
    bt = block.get("type", "")
    data = block.get(bt, {}) or {}
    txt = _rich_text(data.get("rich_text", []))
    if bt == "heading_1":
        return f"# {txt}"
    if bt == "heading_2":
        return f"## {txt}"
    if bt == "heading_3":
        return f"### {txt}"
    if bt == "bulleted_list_item":
        return f"- {txt}"
    if bt == "numbered_list_item":
        return f"1. {txt}"
    if bt == "to_do":
        mark = "x" if data.get("checked") else " "
        return f"- [{mark}] {txt}"
    if bt == "code":
        return f"```{data.get('language', '')}\n{txt}\n```"
    if bt == "quote":
        return f"> {txt}"
    return txt


class NotionPlugin(ConnectorPlugin):
    NAME = "notion"
    URI_SCHEME = "notion"
    DISPLAY_NAME = "Notion"
    PROMPT = "Notion pages as /pages/<id>.md, data sources as /data_sources/<id>/records.jsonl."
    CAPABILITIES = Capabilities(
        manual_sync=True,
        watch=False,
        cursor_kind="last_edited_time",
        full_scan=True,
        delete_detection="full_scan",
        paged_cat=True,
    )

    def __init__(self, config, credential, *, ctx):
        super().__init__(config, credential, ctx=ctx)
        self._client: Optional[AsyncClient] = None

    def _cfg(self, k, d=None):
        return (
            self.config.get(k, d) if isinstance(self.config, dict) else getattr(self.config, k, d)
        )

    async def connect(self) -> None:
        self._client = AsyncClient(auth=self._cfg("token") or self.credential)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def healthcheck(self) -> HealthStatus:
        try:
            await self._client.search(page_size=1)
            return HealthStatus(ok=True)
        except Exception as e:  # noqa: BLE001
            return HealthStatus(ok=False, detail=str(e))

    def _parts(self, path: str) -> list[str]:
        return [p for p in path.strip("/").split("/") if p]

    async def _search(self, object_type: str) -> list[dict]:
        results, cursor = [], None
        while True:
            kw = {"filter": {"property": "object", "value": object_type}, "page_size": 100}
            if cursor:
                kw["start_cursor"] = cursor
            resp = await self._client.search(**kw)
            results.extend(resp.get("results", []))
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        return results

    def object_kind_of(self, path: str) -> ObjectKind:
        if path.endswith(".md"):
            return "document"
        if path.endswith("records.jsonl"):
            return "record_collection"
        if path.endswith("schema.json"):
            return "table_schema"
        return "directory"

    async def stat(self, path: str) -> PathStat:
        if path.endswith(".md"):
            return PathStat(path=path, type="file", media_type="text/markdown")
        if path.endswith("records.jsonl"):
            return PathStat(
                path=path, type="file", media_type="application/x-ndjson", extra={"lazy": True}
            )
        if path.endswith("schema.json"):
            return PathStat(path=path, type="file", media_type="application/json")
        return PathStat(path=path, type="dir")

    async def list(self, path: str) -> list[Entry]:
        parts = self._parts(path)
        if len(parts) == 0:
            return [Entry("pages", "dir"), Entry("data_sources", "dir")]
        if len(parts) == 1 and parts[0] == "pages":
            return [
                Entry(f"{p['id']}.md", "file", "text/markdown") for p in await self._search("page")
            ]
        if len(parts) == 1 and parts[0] == "data_sources":
            return [Entry(d["id"], "dir") for d in await self._search("data_source")]
        if len(parts) == 2 and parts[0] == "data_sources":
            return [
                Entry("schema.json", "file", "application/json"),
                Entry("records.jsonl", "file", "application/x-ndjson", extra={"lazy": True}),
            ]
        return []

    async def read(self, path: str, range: Optional[Range] = None) -> AsyncIterator[bytes]:
        parts = self._parts(path)
        if len(parts) == 2 and parts[0] == "pages" and parts[1].endswith(".md"):
            page_id = parts[1][: -len(".md")]
            lines, cursor = [], None
            while True:
                kw = {"block_id": page_id, "page_size": 100}
                if cursor:
                    kw["start_cursor"] = cursor
                resp = await self._client.blocks.children.list(**kw)
                for b in resp.get("results", []):
                    line = _block_to_md(b)
                    if line:
                        lines.append(line)
                if not resp.get("has_more"):
                    break
                cursor = resp.get("next_cursor")
            yield ("\n".join(lines)).encode()
        else:
            async for chunk in super().read(path, range):
                yield chunk

    @staticmethod
    def _prop_value(prop: dict):
        pt = prop.get("type")
        v = prop.get(pt)
        if pt in ("title", "rich_text"):
            return _rich_text(v)
        if pt == "select":
            return (v or {}).get("name")
        if pt == "multi_select":
            return [x.get("name") for x in (v or [])]
        if pt in ("number", "checkbox", "url", "email", "phone_number"):
            return v
        if pt == "date":
            return (v or {}).get("start")
        if pt == "people":
            return [x.get("name") for x in (v or [])]
        return v

    async def read_records(self, path: str, range: Optional[Range] = None) -> AsyncIterator[dict]:
        parts = self._parts(path)
        if len(parts) == 3 and parts[0] == "data_sources" and parts[2] == "records.jsonl":
            ds_id, cursor = parts[1], None
            while True:
                kw = {"data_source_id": ds_id, "page_size": 100}
                if cursor:
                    kw["start_cursor"] = cursor
                resp = await self._client.data_sources.query(**kw)
                for page in resp.get("results", []):
                    props = page.get("properties", {})
                    rec = {"id": page.get("id")}
                    rec.update({k: self._prop_value(p) for k, p in props.items()})
                    yield rec
                if not resp.get("has_more"):
                    break
                cursor = resp.get("next_cursor")
        elif len(parts) == 3 and parts[0] == "data_sources" and parts[2] == "schema.json":
            ds = await self._client.data_sources.retrieve(data_source_id=parts[1])
            yield {
                "id": parts[1],
                "title": _rich_text(ds.get("title", [])),
                "properties": {k: v.get("type") for k, v in ds.get("properties", {}).items()},
            }

    async def fingerprint(self, path: str) -> Optional[str]:
        return None

    async def sync(self, opts: SyncOptions) -> AsyncIterator[ObjectChange]:
        self.ctx.declare_enumeration("full")
        old = await self.state.get("objects") or {}
        seen: dict[str, str] = {}
        for p in await self._search("page"):
            uri = f"/pages/{p['id']}.md"
            fp = p.get("last_edited_time", "")
            seen[uri] = fp
            if opts.full or old.get(uri) != fp:
                yield ObjectChange(uri, "modified" if uri in old else "added")
        for d in await self._search("data_source"):
            uri = f"/data_sources/{d['id']}/records.jsonl"
            fp = d.get("last_edited_time", "")
            seen[uri] = fp
            if opts.full or old.get(uri) != fp:
                yield ObjectChange(uri, "modified" if uri in old else "added")
        for uri in set(old) - set(seen):
            yield ObjectChange(uri, "deleted")
        await self.state.set("objects", seen)
