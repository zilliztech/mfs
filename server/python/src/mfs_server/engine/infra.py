"""InfraStack — owns the process-level infra clients and their lifecycle.

Constructs the 8 infra clients (meta / milvus / artifact_cache / tx_cache / embed /
converter / vlm / summary) and runs their connect / init / close sequence. Engine
reads them through read-write properties.

Scope: construction + lifecycle only — no business logic, no SQL, no pipeline
singletons. load_builtin() runs in startup. shutdown closes only meta + tx_cache;
milvus and artifact_cache have no lifecycle methods and need no teardown.
"""

from __future__ import annotations

import asyncio
import logging

from ..common.converter import ConverterClient
from ..common.embedding import CachingEmbeddingClient
from ..common.summary import CachingSummaryClient
from ..common.vlm import CachingVlmClient
from ..config import ServerConfig
from ..connectors.registry import load_builtin
from ..storage.artifact_cache import make_artifact_cache
from ..storage.metadata import make_metadata_store
from ..storage.milvus import MilvusStore
from ..storage.transformation_cache import make_transformation_cache

logger = logging.getLogger(__name__)


class InfraStack:
    """Infra clients + their lifecycle, one per Engine instance.

    tx_cache is built before embed/vlm/summary because they cache through it. In
    startup, each store connects before its schema/collection is brought up, and
    preload runs last.
    """

    def __init__(self, cfg: ServerConfig) -> None:
        self.cfg = cfg
        self.ns = cfg.namespace
        # tx_cache first: embed/vlm/summary cache through it.
        self.meta = make_metadata_store(cfg)
        self.milvus = MilvusStore(cfg)
        self.artifact_cache = make_artifact_cache(cfg)
        self.tx_cache = make_transformation_cache(cfg)
        self.embed = CachingEmbeddingClient(cfg, self.tx_cache)
        self.converter = ConverterClient(cfg)
        self.vlm = CachingVlmClient(cfg, self.tx_cache)
        self.summary = CachingSummaryClient(cfg, self.tx_cache)

    async def startup(self, *, preload_local_models: bool = False) -> None:
        # Register built-in connector plugins (idempotent).
        load_builtin()
        # Connect each store, then bring up its schema/collection. Within a store,
        # connect() must precede init_schema() / ensure_collection().
        await self.meta.connect()
        await self.meta.init_schema()
        await self.tx_cache.connect()
        self.milvus.connect()
        self.milvus.ensure_collection(self.ns)
        if preload_local_models:
            await self._preload_models()

    async def _preload_models(self) -> None:
        # Load the local ONNX embedding provider into memory up front so the first
        # embed call doesn't pay the model-load latency. Only providers that opt in.
        if not self.embed.should_preload_on_server_start():
            return
        logger.info(
            "preloading embedding provider %s/%s",
            self.embed.provider_name,
            self.embed.model,
        )
        await asyncio.to_thread(self.embed.preload_provider)
        logger.info("embedding provider ready")

    async def shutdown(self) -> None:
        # Close the connected stores. milvus and artifact_cache have no close method
        # and need no teardown.
        await self.meta.close()
        await self.tx_cache.close()
