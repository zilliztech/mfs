"""ConnectorStateStore: persistent per-connector KV (connector_state table) with
end-of-job commit semantics + mid-job checkpoint.

set() stages into an in-memory pending map; checkpoint()/commit() flush to
connector_state. get() reads pending first, then committed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from ..storage.metadata import MetadataStore


class ConnectorStateStore:
    def __init__(self, meta: MetadataStore, connector_id: str):
        self.meta = meta
        self.cid = connector_id
        self._pending: dict[str, Any] = {}
        self._committed: Optional[dict[str, Any]] = None

    async def _load_committed(self) -> dict[str, Any]:
        if self._committed is None:
            rows = await self.meta.fetchall(
                "SELECT key, value FROM connector_state WHERE connector_id=?", (self.cid,)
            )
            self._committed = {r["key"]: json.loads(r["value"]) for r in rows}
        return self._committed

    async def get(self, key: str) -> Any | None:
        if key in self._pending:
            return self._pending[key]
        return (await self._load_committed()).get(key)

    async def set(self, key: str, value: Any) -> None:
        self._pending[key] = value

    async def delete(self, key: str) -> None:
        self._pending[key] = None  # tombstone

    async def checkpoint(self) -> None:
        await self._flush()

    async def commit(self) -> None:
        await self._flush()

    def snapshot(self) -> dict[str, Any]:
        """The staged-but-uncommitted state (for deferring an enqueued job's commit to
        the worker's success path)."""
        return dict(self._pending)

    async def apply(self, data: dict[str, Any]) -> None:
        """Commit a previously-snapshotted state dict (worker applies it only after the
        enqueued job's tasks all succeed)."""
        self._pending.update(data)
        await self._flush()

    async def _flush(self) -> None:
        if not self._pending:
            return
        now = datetime.now(timezone.utc).isoformat()
        committed = await self._load_committed()
        for k, v in self._pending.items():
            if v is None:
                await self.meta.execute(
                    "DELETE FROM connector_state WHERE connector_id=? AND key=?", (self.cid, k)
                )
                committed.pop(k, None)
            else:
                await self.meta.execute(
                    "INSERT INTO connector_state (connector_id, key, value, updated_at) "
                    "VALUES (?,?,?,?) ON CONFLICT(connector_id, key) DO UPDATE SET "
                    "value=excluded.value, updated_at=excluded.updated_at",
                    (self.cid, k, json.dumps(v), now),
                )
                committed[k] = v
        self._pending = {}
