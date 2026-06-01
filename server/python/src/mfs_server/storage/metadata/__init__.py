"""Metadata DB: dual-backend (SQLite + Postgres) with a shared contract.

Public surface:

    from mfs_server.storage.metadata import MetadataStore, make_metadata_store
    store = make_metadata_store(cfg)        # dispatch on cfg.metadata.backend
    isinstance(store, MetadataStore)        # True (ABC)

For back-compat with the pre-refactor single-file `MetadataStore(cfg)` call site,
`MetadataStore(cfg)` is intentionally NOT instantiable — call `make_metadata_store()`.
The name is exported as the ABC type so existing type hints
(`m: MetadataStore`) keep working without code changes.
"""

from __future__ import annotations

from ...config import ServerConfig
from .base import CURRENT_SCHEMA_VERSION, SQLITE_DDL, MetadataStoreBase

# `MetadataStore` is the canonical name in type hints across the codebase.
MetadataStore = MetadataStoreBase


def make_metadata_store(cfg: ServerConfig) -> MetadataStoreBase:
    """Factory: dispatch on cfg.metadata.backend."""
    backend = cfg.metadata.backend
    if backend == "postgres":
        from .postgres import PostgresMetadataStore

        return PostgresMetadataStore(cfg)
    if backend == "sqlite":
        from .sqlite import SqliteMetadataStore

        return SqliteMetadataStore(cfg)
    raise NotImplementedError(f"metadata backend {backend!r} not supported")


__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "SQLITE_DDL",
    "MetadataStore",
    "MetadataStoreBase",
    "make_metadata_store",
]
