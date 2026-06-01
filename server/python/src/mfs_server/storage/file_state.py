"""file_state DAO — file connector's per-path manifest.
Backed by the file_state table in the metadata DB (shares its connection).
"""

from __future__ import annotations

from typing import Optional

from .metadata import MetadataStore


class FileStateStore:
    def __init__(self, meta: MetadataStore, namespace_id: str, connector_id: str):
        self.meta = meta
        self.ns = namespace_id
        self.cid = connector_id

    async def get(self, path: str) -> Optional[dict]:
        return await self.meta.fetchone(
            "SELECT * FROM file_state WHERE namespace_id=? AND connector_id=? AND path=?",
            (self.ns, self.cid, path),
        )

    async def all_rows(self) -> list[dict]:
        return await self.meta.fetchall(
            "SELECT * FROM file_state WHERE namespace_id=? AND connector_id=?",
            (self.ns, self.cid),
        )

    async def all_paths(self) -> set[str]:
        rows = await self.meta.fetchall(
            "SELECT path FROM file_state WHERE namespace_id=? AND connector_id=?",
            (self.ns, self.cid),
        )
        return {r["path"] for r in rows}

    async def upsert(
        self,
        path: str,
        size: int,
        mtime_ns: int,
        inode: Optional[int],
        sha1: str,
        status: str = "indexed",
        renamed_from: Optional[str] = None,
        indexed_at: Optional[str] = None,
    ) -> None:
        await self.meta.execute(
            "INSERT INTO file_state (namespace_id, connector_id, path, size, mtime_ns, inode, "
            " sha1, status, renamed_from, indexed_at) VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(namespace_id, connector_id, path) DO UPDATE SET "
            " size=excluded.size, mtime_ns=excluded.mtime_ns, inode=excluded.inode, "
            " sha1=excluded.sha1, status=excluded.status, renamed_from=excluded.renamed_from, "
            " indexed_at=excluded.indexed_at",
            (
                self.ns,
                self.cid,
                path,
                size,
                mtime_ns,
                inode,
                sha1,
                status,
                renamed_from,
                indexed_at,
            ),
        )

    async def update_mtime(self, path: str, mtime_ns: int) -> None:
        await self.meta.execute(
            "UPDATE file_state SET mtime_ns=? WHERE namespace_id=? AND connector_id=? AND path=?",
            (mtime_ns, self.ns, self.cid, path),
        )

    async def mark_indexed(self, path: str, indexed_at: str) -> None:
        await self.meta.execute(
            "UPDATE file_state SET status='indexed', renamed_from=NULL, indexed_at=? "
            "WHERE namespace_id=? AND connector_id=? AND path=?",
            (indexed_at, self.ns, self.cid, path),
        )

    async def delete(self, path: str) -> None:
        await self.meta.execute(
            "DELETE FROM file_state WHERE namespace_id=? AND connector_id=? AND path=?",
            (self.ns, self.cid, path),
        )

    async def rename(self, old_path: str, new_path: str) -> None:
        """Carry the old row's fingerprint to new path."""
        old = await self.get(old_path)
        if old is None:
            return
        await self.delete(old_path)
        await self.upsert(
            new_path,
            old["size"],
            old["mtime_ns"],
            old["inode"],
            old["sha1"],
            status=old["status"],
            renamed_from=old_path,
            indexed_at=old["indexed_at"],
        )
