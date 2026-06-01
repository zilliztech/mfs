"""Transformation cache — content-addressable memo store for convert / embed /
vlm / summary results. Two backends: SqliteTransformationCache (default),
PostgresTransformationCache (CS deployments). The cache is best-effort —
losing it costs recompute, never correctness.

Public surface:

    from mfs_server.storage.transformation_cache import TransformationCache, make_transformation_cache
    tc = make_transformation_cache(cfg)
    isinstance(tc, TransformationCache)        # True (ABC)

`TransformationCache(cfg)` is exported as the ABC type for type hints (back-compat
with the pre-refactor single-class shape); use `make_transformation_cache()` to
construct an instance.
"""

from __future__ import annotations

from ...config import ServerConfig
from .base import SCHEMA, TransformationCacheBase

# Canonical name used in type hints across the codebase.
TransformationCache = TransformationCacheBase


def make_transformation_cache(cfg: ServerConfig) -> TransformationCacheBase:
    """Factory: dispatch on cfg.transformation_cache.backend."""
    backend = cfg.transformation_cache.backend
    if backend == "postgres":
        from .postgres import PostgresTransformationCache

        return PostgresTransformationCache(cfg)
    if backend == "sqlite":
        from .sqlite import SqliteTransformationCache

        return SqliteTransformationCache(cfg)
    raise NotImplementedError(f"transformation_cache backend {backend!r} not supported")


__all__ = [
    "SCHEMA",
    "TransformationCache",
    "TransformationCacheBase",
    "make_transformation_cache",
]
