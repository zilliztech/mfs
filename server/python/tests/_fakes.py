"""Shared in-memory fakes for the producer unit tests.

No live network / DB / Milvus: a fake connector plugin, an in-memory artifact store
(backed by tmp files for streaming materialization), and fake converter / vlm / summary
clients that record calls + in-flight concurrency.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from typing import Any, Optional

from mfs_server.config import ServerConfig
from mfs_server.engine.producers import ConcurrencyGate, ProducerContext


class FakeConnCtx:
    """Stand-in for ConnectorContext: only was_partial / declare_partial are used."""

    def __init__(self) -> None:
        self._partial: set[str] = set()

    def declare_partial(self, path: str) -> None:
        self._partial.add(path)

    def was_partial(self, path: str) -> bool:
        return path in self._partial


class FakePlugin:
    """Minimal ConnectorPlugin: byte objects via read(), record objects via read_records()."""

    def __init__(
        self,
        *,
        data: Optional[dict[str, bytes]] = None,
        records: Optional[dict[str, list[dict]]] = None,
    ) -> None:
        self._data = dict(data or {})
        self._records = dict(records or {})
        self.ctx = FakeConnCtx()
        self.aclosed: list[str] = []  # paths whose record generator was aclose()d

    async def read(self, path: str, range: Any = None):
        yield self._data[path]

    def read_records(self, path: str, range: Any = None):
        if path not in self._records:
            return None
        recs = self._records[path]
        aclosed = self.aclosed

        async def gen():
            try:
                for r in recs:
                    yield r
            finally:
                aclosed.append(path)

        return gen()


class FakeArtifactStore:
    """In-memory ArtifactStore; artifact_path returns a real tmp file for streaming."""

    def __init__(self, root: str) -> None:
        self.root = str(root)
        self.store: dict[tuple[str, str, str], bytes] = {}
        self.currency: dict[tuple[str, str, str], str] = {}

    async def put_artifact(
        self, ns: str, uri: str, kind: str, data: bytes, currency: str = ""
    ) -> None:
        self.store[(ns, uri, kind)] = data
        self.currency[(ns, uri, kind)] = currency

    async def get_artifact(self, ns: str, uri: str, kind: str) -> Optional[bytes]:
        return self.store.get((ns, uri, kind))

    async def get_artifact_fresh(
        self, ns: str, uri: str, kind: str, currency: str
    ) -> Optional[bytes]:
        if self.currency.get((ns, uri, kind)) != currency:
            return None
        return self.store.get((ns, uri, kind))

    def artifact_path(self, ns: str, uri: str, kind: str) -> str:
        safe = hashlib.sha1(f"{ns}|{uri}|{kind}".encode()).hexdigest()
        return os.path.join(self.root, f"{safe}.bin")


class FakeVlm:
    """describe() memoizes by image bytes (cache hit/miss) and tracks in-flight calls."""

    def __init__(self, delay: float = 0.02, reply: Optional[str] = None) -> None:
        self.api_calls = 0
        self.cache_hits = 0
        self.delay = delay
        self.reply = reply
        self._cache: dict[str, str] = {}
        self._inflight = 0
        self.max_inflight = 0

    async def describe(self, data: bytes, ext: str) -> str:
        key = hashlib.sha1(data).hexdigest()
        if key in self._cache:
            self.cache_hits += 1
            return self._cache[key]
        self._inflight += 1
        self.max_inflight = max(self.max_inflight, self._inflight)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self._inflight -= 1
        self.api_calls += 1
        desc = self.reply if self.reply is not None else f"vlm[{ext}]:{key[:8]}"
        self._cache[key] = desc
        return desc


class FakeSummary:
    def __init__(self, delay: float = 0.01, reply: Optional[str] = None) -> None:
        self.calls = 0
        self.delay = delay
        self.reply = reply
        self._inflight = 0
        self.max_inflight = 0

    async def summarize(self, text: str, kind: str = "directory_summary") -> str:
        self._inflight += 1
        self.max_inflight = max(self.max_inflight, self._inflight)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self._inflight -= 1
        self.calls += 1
        return self.reply if self.reply is not None else f"summary[{kind}]:{text[:40]}"


class FakeConverter:
    def __init__(self) -> None:
        self.calls = 0

    def identity(self) -> str:
        return "markitdown.markitdown.1"

    def currency(self, data: bytes) -> str:
        import hashlib

        return f"{hashlib.sha1(data).hexdigest()}:{self.identity()}"

    async def convert(self, data: bytes, ext: str) -> str:
        self.calls += 1
        return f"# Converted {ext}\n\n" + data.decode("utf-8", errors="replace")


def build_ctx(
    *,
    artifacts: FakeArtifactStore,
    chunk_size: int = 2048,
    description_concurrency: int = 2,
    summary_concurrency: int = 2,
    vlm: Any = None,
    summary: Any = None,
    converter: Any = None,
) -> ProducerContext:
    cfg = ServerConfig()
    cfg.chunking.chunk_size = chunk_size
    return ProducerContext(
        cfg=cfg,
        namespace_id="default",
        artifacts=artifacts,
        converter=converter or FakeConverter(),
        vlm=vlm or FakeVlm(),
        summary=summary or FakeSummary(),
        description_gate=ConcurrencyGate(description_concurrency),
        summary_gate=ConcurrencyGate(summary_concurrency),
    )


async def collect(producer: Any, task: Any) -> list:
    items = []
    async for x in producer.produce(task):
        items.append(x)
    return items
