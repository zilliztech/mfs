"""BigQuery connector — structured, table_rows.
google-cloud-bigquery (sync client; wrapped in asyncio.to_thread). Layout
/<dataset>/tables/<table>/{schema.json,rows.jsonl}. read_records streams rows
as dicts; framework's table_rows pipeline does per_row chunk + locator.

API verified against google-cloud-bigquery docs (Client.list_datasets /
list_tables(dataset) / get_table(ref).schema -> SchemaField(.name/.field_type) /
query(sql).result() -> Row, dict(row)). NOT end-to-end tested (needs GCP creds).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Optional

from google.cloud import bigquery

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
    safe_ident,
)


class BigQueryPlugin(ConnectorPlugin):
    NAME = "bigquery"
    URI_SCHEME = "bigquery"
    DISPLAY_NAME = "BigQuery"
    PROMPT = "BigQuery tables as /<dataset>/tables/<table>/rows.jsonl + schema.json."
    CAPABILITIES = Capabilities(
        manual_sync=True,
        watch=False,
        cursor_kind="modified",
        full_scan=True,
        delete_detection="full_scan",
        paged_cat=True,
    )

    def __init__(self, config, credential, *, ctx):
        super().__init__(config, credential, ctx=ctx)
        self._client: Optional[bigquery.Client] = None

    def _cfg(self, k, d=None):
        return (
            self.config.get(k, d) if isinstance(self.config, dict) else getattr(self.config, k, d)
        )

    async def connect(self) -> None:
        # project + ADC / service-account JSON path via GOOGLE_APPLICATION_CREDENTIALS.
        # `endpoint` points at a self-hosted/emulator BigQuery (anonymous creds).
        endpoint = self._cfg("endpoint")
        if endpoint:
            from google.api_core.client_options import ClientOptions
            from google.auth.credentials import AnonymousCredentials

            self._client = await asyncio.to_thread(
                lambda: bigquery.Client(
                    project=self._cfg("project"),
                    client_options=ClientOptions(api_endpoint=endpoint),
                    credentials=AnonymousCredentials(),
                )
            )
        else:
            self._client = await asyncio.to_thread(bigquery.Client, project=self._cfg("project"))

    async def close(self) -> None:
        if self._client is not None:
            await asyncio.to_thread(self._client.close)
            self._client = None

    async def healthcheck(self) -> HealthStatus:
        try:
            await asyncio.to_thread(lambda: list(self._client.list_datasets(max_results=1)))
            return HealthStatus(ok=True)
        except Exception as e:  # noqa: BLE001
            return HealthStatus(ok=False, detail=str(e))

    def _parts(self, path: str) -> list[str]:
        return [p for p in path.strip("/").split("/") if p]

    async def _datasets(self) -> list[str]:
        cfg = self._cfg("datasets")
        if cfg:
            return list(cfg)
        return await asyncio.to_thread(lambda: [d.dataset_id for d in self._client.list_datasets()])

    async def _tables(self, dataset: str) -> list[str]:
        return await asyncio.to_thread(
            lambda: [t.table_id for t in self._client.list_tables(dataset)]
        )

    def object_kind_of(self, path: str) -> ObjectKind:
        if path.endswith("rows.jsonl"):
            return "table_rows"
        if path.endswith("schema.json"):
            return "table_schema"
        return "directory"

    async def stat(self, path: str) -> PathStat:
        if path.endswith("rows.jsonl"):
            return PathStat(
                path=path,
                type="file",
                media_type="application/x-ndjson",
                fingerprint=await self.fingerprint(path),
                extra={"lazy": True},
            )
        if path.endswith("schema.json"):
            return PathStat(path=path, type="file", media_type="application/json")
        return PathStat(path=path, type="dir")

    async def list(self, path: str) -> list[Entry]:
        parts = self._parts(path)
        if len(parts) == 0:
            return [Entry(d, "dir") for d in await self._datasets()]
        if len(parts) == 1:
            return [Entry("tables", "dir")]
        if len(parts) == 2 and parts[1] == "tables":
            return [Entry(t, "dir") for t in await self._tables(parts[0])]
        if len(parts) == 3:
            return [
                Entry("schema.json", "file", "application/json"),
                Entry("rows.jsonl", "file", "application/x-ndjson", extra={"lazy": True}),
            ]
        return []

    def _table_ref(self, dataset: str, table: str) -> str:
        proj = self._client.project
        return f"`{proj}`.`{dataset}`.`{table}`"

    async def read_records(self, path: str, range: Optional[Range] = None) -> AsyncIterator[dict]:
        parts = self._parts(path)
        # /<dataset>/tables/<table>/{rows.jsonl,schema.json}
        if len(parts) == 4 and parts[1] == "tables" and parts[3] == "rows.jsonl":
            dataset, table = safe_ident(parts[0]), safe_ident(parts[2])
            lim = self._cfg("max_read_rows", 100000)
            ref = f"{self._client.project}.{dataset}.{table}"
            # list_rows (tabledata.list) instead of a SELECT * query: no query-job billing,
            # native start_index/max_results paging for cat --range pushdown.
            if range is not None:
                start = max(0, int(range.start))
                cnt = max(0, int(range.end) - start)
                it = await asyncio.to_thread(
                    lambda: list(self._client.list_rows(ref, start_index=start, max_results=cnt))
                )
            else:
                tbl = await asyncio.to_thread(self._client.get_table, ref)
                if tbl.num_rows is not None and tbl.num_rows > lim:
                    self.ctx.declare_partial(path)  # capped -> search_status=partial
                it = await asyncio.to_thread(
                    lambda: list(self._client.list_rows(ref, max_results=lim))
                )
            for r in it:
                yield dict(r)
        elif len(parts) == 4 and parts[1] == "tables" and parts[3] == "schema.json":
            dataset, table = safe_ident(parts[0]), safe_ident(parts[2])
            tbl = await asyncio.to_thread(
                self._client.get_table, f"{self._client.project}.{dataset}.{table}"
            )
            yield {
                "dataset": dataset,
                "table": table,
                "columns": [
                    {"name": f.name, "type": f.field_type, "mode": f.mode} for f in tbl.schema
                ],
            }

    async def fingerprint(self, path: str) -> Optional[str]:
        parts = self._parts(path)
        if len(parts) == 4 and parts[1] == "tables" and parts[3] == "rows.jsonl":
            tbl = await asyncio.to_thread(
                self._client.get_table,
                f"{self._client.project}.{safe_ident(parts[0])}.{safe_ident(parts[2])}",
            )
            return f"rows:{tbl.num_rows}:{tbl.modified.isoformat() if tbl.modified else ''}"
        if len(parts) == 4 and parts[1] == "tables" and parts[3] == "schema.json":
            tbl = await asyncio.to_thread(
                self._client.get_table,
                f"{self._client.project}.{safe_ident(parts[0])}.{safe_ident(parts[2])}",
            )
            return "schema:" + ";".join(f"{f.name}:{f.field_type}" for f in tbl.schema)
        return None

    async def sync(self, opts: SyncOptions) -> AsyncIterator[ObjectChange]:
        self.ctx.declare_enumeration("full")
        old = await self.state.get("tables") or {}
        seen: dict[str, str] = {}
        for dataset in await self._datasets():
            for table in await self._tables(dataset):
                for leaf in ("schema.json", "rows.jsonl"):
                    p = f"/{dataset}/tables/{table}/{leaf}"
                    fp = await self.fingerprint(p) or ""
                    seen[p] = fp
                    if opts.full or old.get(p) != fp:
                        yield ObjectChange(p, "modified" if p in old else "added")
        for p in set(old) - set(seen):
            yield ObjectChange(p, "deleted")
        await self.state.set("tables", seen)
