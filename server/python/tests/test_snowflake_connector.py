from __future__ import annotations

import pytest

pytest.importorskip("snowflake.connector")

from mfs_server.connectors.snowflake.plugin import SnowflakePlugin
from mfs_server.connectors.base import Range


class _Ctx:
    def __init__(self) -> None:
        self.state = None
        self.partials: list[str] = []

    def declare_partial(self, path: str) -> None:
        self.partials.append(path)


class _Plugin(SnowflakePlugin):
    def __init__(self) -> None:
        self.ctx = _Ctx()
        self.queries: list[str] = []
        super().__init__({"max_read_rows": 2}, "", ctx=self.ctx)

    async def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        self.queries.append(sql)
        if "count(*)" in sql:
            return [{"N": 3}]
        return [{"ID": 1}, {"ID": 2}]


async def test_snowflake_rows_mark_partial_when_capped() -> None:
    plugin = _Plugin()

    rows = [row async for row in plugin.read_records("/PROD/PUBLIC/tables/TICKETS/rows.jsonl")]

    assert rows == [{"ID": 1}, {"ID": 2}]
    assert plugin.ctx.partials == ["/PROD/PUBLIC/tables/TICKETS/rows.jsonl"]
    assert any("ORDER BY 1 LIMIT 2" in query for query in plugin.queries)


async def test_snowflake_rows_push_down_range() -> None:
    plugin = _Plugin()

    rows = [
        row
        async for row in plugin.read_records(
            "/PROD/PUBLIC/tables/TICKETS/rows.jsonl", Range(start=5, end=7)
        )
    ]

    assert rows == [{"ID": 1}, {"ID": 2}]
    assert plugin.ctx.partials == []
    assert plugin.queries == ['SELECT * FROM "PROD"."PUBLIC"."TICKETS" ORDER BY 1 LIMIT 2 OFFSET 5']
