"""Driver: the global ChunksProducer pool + claim loop (§3.1 / §5.7).

N producer coroutines claim ObjectTasks from `object_tasks` GLOBALLY — no per-job filter
(§5.7) — ordered by priority then age, so a late high-priority job interleaves with an
older one (fairness, §5.6). Each claimed task is dispatched to its per-okind producer
(step 1), whose Chunk stream is forwarded through the shared chunks_q to the process-level
EmbedConsumer (step 2). When the consumer finishes a task (all chunks written + EndOfTask
seen) it fires the success hook registered here, which flips the object_tasks row to
'succeeded' (the Map→Reduce notification of §6.4.4 hangs off the same hook in step 4).

Standalone for now: engine.py / _drain_job are NOT modified — wiring into the real
ingest flow is step 4. The claim SQL lives only here; engine._claim_batch is untouched.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from ..storage.ids import chunk_id
from .adapters import EmbedderAdapter, MilvusSinkAdapter, TxCacheAdapter
from .pipeline import EmbedConsumer, TaskEnvelope, make_chunks_q
from .producers import select_producer
from .producers.base import EndOfTask, ObjectTask, ProducerContext, cap_content

# Process-global producer pool size (§3.1 default 8). The TOML key [chunks_producer]
# .concurrency lands in step 11; until then callers pass `concurrency` explicitly.
_DEFAULT_CONCURRENCY = 8

# row dict -> (plugin, connector_uri). Resolves a task's connector to its plugin instance
# and scheme prefix; injected so the driver stays decoupled from the plugin registry.
PluginResolver = Callable[[dict], "tuple[Any, str]"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChunksProducerPool:
    """Spawns `concurrency` coroutines that drain pending object_tasks into chunks_q."""

    def __init__(
        self,
        *,
        meta: Any,
        chunks_q: asyncio.Queue,
        ctx: ProducerContext,
        consumer: EmbedConsumer,
        resolve_plugin: PluginResolver,
        concurrency: int = _DEFAULT_CONCURRENCY,
    ):
        self._meta = meta
        self._q = chunks_q
        self._ctx = ctx
        self._consumer = consumer
        self._resolve = resolve_plugin
        self._concurrency = max(1, concurrency)
        # full_uri -> object_tasks.id for in-flight tasks; the success hook gets task_uri
        # (full uri) + job_id and needs the row id to flip status.
        self._inflight: dict[str, str] = {}
        consumer.register_on_succeeded(self._on_task_succeeded)

    async def run(self) -> None:
        """Run all workers until no pending tasks remain, then return."""
        await asyncio.gather(*[self._worker() for _ in range(self._concurrency)])

    # --- claim loop ---
    async def _worker(self) -> None:
        while True:
            row = await self._claim_one()
            if row is None:
                return  # no pending tasks left for this worker
            await self._process(row)

    async def _claim_one(self) -> Optional[dict]:
        """Claim the highest-priority pending task, globally (§5.7).

        Race-safe across N coroutines without holding a writer lock: SELECT the top
        candidate, then a conditional `UPDATE ... WHERE id=? AND status='pending'`. Only
        the coroutine whose UPDATE flips a row (rowcount==1) wins it; a loser re-SELECTs
        the next candidate. SQLite serializes the short writes, so no long-held lock."""
        while True:
            rows = await self._meta.fetchall(
                "SELECT * FROM object_tasks WHERE status='pending' "
                "ORDER BY priority ASC, started_at ASC LIMIT 1"
            )
            if not rows:
                return None
            row = rows[0]
            won = await self._meta.execute_rowcount(
                "UPDATE object_tasks SET status='running', started_at=?, attempts=attempts+1 "
                "WHERE id=? AND status='pending'",
                (_now(), row["id"]),
            )
            if won == 1:
                return row
            await asyncio.sleep(0)  # lost the race for this row; yield and re-select

    # --- per-task processing ---
    async def _process(self, row: dict) -> None:
        plugin, connector_uri = self._resolve(row)
        rel = row["object_uri"]
        full_uri = connector_uri + rel
        job_id = row["connector_job_id"]
        task_id = row["id"]
        self._inflight[full_uri] = task_id
        try:
            okind = plugin.object_kind_of(rel)
            producer = select_producer(okind, self._ctx)
            if producer is None:
                # binary / directory / unsupported okind: no chunks. A bare EndOfTask
                # lets the consumer finalize it (purge stale + mark succeeded).
                await self._q.put(self._envelope(task_id, connector_uri, full_uri, job_id, EndOfTask()))
                return
            task = ObjectTask(
                object_uri=rel,
                connector_uri=connector_uri,
                okind=okind,
                change_kind=row["change_kind"],
                connector_job_id=job_id,
                task_id=task_id,
                plugin=plugin,
                ocfg=plugin.ctx.object_config_for(rel),
            )
            async for item in producer.produce(task):
                if isinstance(item, EndOfTask):
                    await self._q.put(self._envelope(task_id, connector_uri, full_uri, job_id, item))
                else:
                    # producers leave uri/job_id optional; backfill from the task identity.
                    item.uri = item.uri or full_uri
                    item.connector_job_id = item.connector_job_id or job_id
                    await self._q.put(self._envelope(task_id, connector_uri, full_uri, job_id, item))
        except Exception as e:  # noqa: BLE001 — any per-task failure is contained to that task
            self._inflight.pop(full_uri, None)
            await self._fail(task_id, e)

    @staticmethod
    def _envelope(task_id, connector_uri, full_uri, job_id, payload) -> TaskEnvelope:
        return TaskEnvelope(
            task_id=task_id,
            task_uri=full_uri,
            connector_uri=connector_uri,
            job_id=job_id,
            payload=payload,
        )

    # --- terminal transitions ---
    async def _on_task_succeeded(
        self, task_uri: str, job_id: Optional[str], chunk_count: int = 0, partial: bool = False
    ) -> None:
        """EmbedConsumer success hook: all of this object's chunks are in Milvus."""
        task_id = self._inflight.pop(task_uri, None)
        if task_id is None:
            return
        await self._meta.execute(
            "UPDATE object_tasks SET status='succeeded', finished_at=? WHERE id=?",
            (_now(), task_id),
        )

    async def _fail(self, task_id: str, exc: Exception) -> None:
        # attempts was already incremented at claim time (mirrors engine._claim_batch),
        # so failure only records the terminal status + error.
        await self._meta.execute(
            "UPDATE object_tasks SET status='failed', last_error=?, finished_at=? WHERE id=?",
            (str(exc)[:2000], _now(), task_id),
        )


async def drain_pending(
    *,
    meta: Any,
    ctx: ProducerContext,
    consumer: EmbedConsumer,
    resolve_plugin: PluginResolver,
    batch_size: int,
    concurrency: int = _DEFAULT_CONCURRENCY,
    chunks_q: Optional[asyncio.Queue] = None,
) -> ChunksProducerPool:
    """Drive a full drain: start the consumer, run the pool until no pending tasks remain,
    then drain the queue and finalize. Returns the pool (for inspection in tests)."""
    q = chunks_q if chunks_q is not None else make_chunks_q(batch_size)
    consumer.start(q)
    pool = ChunksProducerPool(
        meta=meta,
        chunks_q=q,
        ctx=ctx,
        consumer=consumer,
        resolve_plugin=resolve_plugin,
        concurrency=concurrency,
    )
    await pool.run()  # all production enqueued
    await consumer.shutdown()  # drain queue + fire success hooks
    return pool


class _WiredEmbedConsumer(EmbedConsumer):
    """EmbedConsumer wired for the real Milvus + embedding cache: overrides the two
    hooks step 2 left open — the embed cache key (provider/model/version aware) and the
    Milvus row shape (chunk_id PK + namespace_id + indexed_at)."""

    def __init__(self, *args, namespace_id: str, embed_key_fn, **kwargs):
        super().__init__(*args, **kwargs)
        self._ns = namespace_id
        self._embed_key_fn = embed_key_fn

    def _cache_key(self, chunk) -> str:
        return self._embed_key_fn(chunk.content)

    def _build_row(self, env: TaskEnvelope, chunk, vec: list[float]) -> dict:
        content, _ = cap_content(chunk.content)
        loc = chunk.locator
        return {
            "chunk_id": chunk_id(self._ns, env.connector_uri, env.task_uri, chunk.chunk_kind, loc),
            "namespace_id": self._ns,
            "connector_uri": env.connector_uri,
            "object_uri": env.task_uri,
            "locator": loc,
            "content": content,
            "dense_vec": vec,
            "chunk_kind": chunk.chunk_kind,
            "metadata": chunk.metadata,
            "indexed_at": int(time.time() * 1000),
        }


def build_pipeline_consumer(
    *,
    embed_client: Any,
    milvus: Any,
    tx_cache: Any,
    namespace_id: str,
    batch_size: int,
    idle_ms: Optional[int] = None,
) -> EmbedConsumer:
    """Assemble a Milvus/embedding-wired EmbedConsumer from engine.py's existing clients
    (for step 4). Tests construct their own consumer with fakes instead."""
    embedder = EmbedderAdapter(embed_client._embed_api)  # raw provider call — see EmbedderAdapter
    sink = MilvusSinkAdapter(milvus, namespace_id)
    tx = TxCacheAdapter(
        tx_cache,
        kind="embedding",
        provider=embed_client.provider_name,
        model=embed_client.model,
        version=embed_client.version,
    )
    kwargs = {} if idle_ms is None else {"idle_ms": idle_ms}
    return _WiredEmbedConsumer(
        embedder,
        sink,
        tx,
        batch_size,
        namespace_id=namespace_id,
        embed_key_fn=embed_client._key,
        **kwargs,
    )
