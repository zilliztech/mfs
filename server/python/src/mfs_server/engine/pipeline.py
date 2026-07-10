"""Pipeline tail: chunks_q + the process-level EmbedConsumer (§3.1 / §5.1 / §5.2).

The Object Lane (per-object producers) and the Job Lane (directory summaries) both emit Chunk
streams into a single bounded `chunks_q` (§5.1 — embed is the real bottleneck, so one queue
decouples chunk production from embed consumption). One process-level EmbedConsumer (§5.2)
drains that queue across ALL connector jobs so embed batches always fill to `batch_size`,
then upserts to Milvus.

Per-object atomicity (§6.1) is enforced here: `delete_by_object` runs once per task
(on its first chunk, before any upsert), and a per-task pending counter + the
`EndOfTask` sentinel decide when a task is fully written — at which point the
on_object_task_succeeded hooks fire (the objects-table + Job Lane completion update).

Queue transport — `Chunk` carries `uri` + `connector_job_id`, but `EndOfTask` is a bare
identity-less sentinel (producers/base.py). Since this consumer interleaves many tasks
on one queue, every queue item is a `TaskEnvelope` pairing the payload with explicit
task identity. The engine builds the envelopes from each ObjectTask's producer output;
this file does NOT redefine the base Chunk / EndOfTask types.

No real Milvus / embedder here — the three injected Protocols are mocked in tests; the
adapters that bind them to the real CachingEmbeddingClient / MilvusStore / tx_cache (and
that add chunk_id + namespace to rows) live in adapters.py.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from _asyncio import Task
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, Protocol, Union, runtime_checkable

from .producers.base import Chunk, EndOfTask, cap_content

logger = logging.getLogger(__name__)

# --- internal constants (not TOML-exposed, §7.3) ---
_EMBED_FLUSH_IDLE_MS = 5000  # force a flush when the batch sits idle this long
_CHUNKS_Q_MIN_MAXSIZE = 200  # floor for the bounded chunks_q (derived from batch_size)


def _make_chunks_q_maxsize(batch_size: int) -> int:
    """chunks_q bound = max(200, batch_size * 2) (§3.1 / §7.3 _CHUNKS_Q_MAXSIZE)."""
    return max(_CHUNKS_Q_MIN_MAXSIZE, batch_size * 2)


def make_chunks_q(batch_size: int) -> asyncio.Queue:
    """Bounded process-level chunks_q. Bounded = backpressure: producers block when the
    consumer falls behind, so a runaway producer can't blow up memory (§4.4)."""
    return asyncio.Queue(maxsize=_make_chunks_q_maxsize(batch_size))


# --- injected interfaces (Protocols; mocked in tests) ---


@runtime_checkable
class Embedder(Protocol):
    """Raw embedder — the EmbedConsumer owns the cache layer around it (§6.3)."""

    async def batch_embed(self, texts: list[str]) -> list[list[float]]: ...


@runtime_checkable
class MilvusSink(Protocol):
    async def upsert(self, rows: list[dict]) -> None: ...

    async def delete_by_object(self, connector_uri: str, object_uri: str) -> None: ...


@runtime_checkable
class TxCacheLike(Protocol):
    """Per-input embed-vector cache (§6.3 — key is per-input, batch-boundary free)."""

    async def batch_get(self, keys: list[str]) -> dict[str, list[float]]: ...

    async def batch_put(self, items: dict[str, list[float]]) -> None: ...


# --- queue transport ---


@dataclass
class TaskEnvelope:
    """One chunks_q item: a producer payload (Chunk | EndOfTask) + its task identity.

    task_id    — pending-counter key (the object_tasks row id).
    task_uri   — full object uri; passed to delete_by_object + the success hook.
    connector_uri — for delete_by_object(connector_uri, object_uri).
    job_id     — connector_job_id, passed to the success hook (Job Lane completion, §6.4.4)."""

    task_id: str
    task_uri: str
    connector_uri: str
    job_id: Optional[str]
    payload: Union[Chunk, EndOfTask]


def chunk_envelope(
    task_id: str, connector_uri: str, task_uri: str, job_id: Optional[str], chunk: Chunk
) -> TaskEnvelope:
    return TaskEnvelope(task_id, task_uri, connector_uri, job_id, chunk)


def end_of_task_envelope(
    task_id: str, connector_uri: str, task_uri: str, job_id: Optional[str], eot: EndOfTask
) -> TaskEnvelope:
    return TaskEnvelope(task_id, task_uri, connector_uri, job_id, eot)


# Internal sentinel to unblock run()'s queue.get() on shutdown. Distinct from EndOfTask.
class _Shutdown:
    pass


_SHUTDOWN = _Shutdown()

# Success hook: (task_uri, job_id, chunk_count, partial, error). chunk_count is the total number
# of chunks actually persisted to Milvus for the object (credited only on a successful flush,
# post-dedup — never on a chunk merely received, and never on a batch that failed and was
# dropped); partial is True if any chunk's content was capped, the task was truncated
# (EndOfTask.partial), or a flush dropped some of its chunks. error is None on a
# clean finalize, or "<ExcType>: <msg>" when an embed/upsert flush failed for this task — the
# callback uses it to record a failed status + last_error. Callers derive search_status from
# these without the producer returning them inline (§6.1).
SuccessCallback = Callable[
    [str, Optional[str], int, bool, Optional[str]], Union[None, Awaitable[None]]
]


class EmbedConsumer:
    """Process-level singleton: drain chunks_q across all jobs, embed, upsert (§5.2).

    `tx_cache` is an injected dependency (TxCacheLike): flush() needs it for the vector
    cache layer."""

    def __init__(
        self,
        embedder: Embedder,
        milvus: MilvusSink,
        tx_cache: TxCacheLike,
        batch_size: int,
        idle_ms: int = _EMBED_FLUSH_IDLE_MS,
    ) -> None:
        self._embedder = embedder
        self._milvus = milvus
        self._tx_cache = tx_cache
        self._batch_size = batch_size
        self._idle_ms = idle_ms

        # accumulated (envelope, chunk) awaiting flush
        self._batch: list[tuple[TaskEnvelope, Chunk]] = []
        # per-task bookkeeping (§6.1)
        self._pending: dict[str, int] = {}  # task_id -> chunks received, not yet written
        self._eot: dict[str, bool] = {}  # task_id -> partial flag (presence = EndOfTask seen)
        self._deleted: set[str] = set()  # task_ids whose stale chunks were purged
        self._meta: dict[str, TaskEnvelope] = {}  # task_id -> last seen envelope (identity)
        # per-task accounting for the success hook: cumulative chunk count (never
        # decremented, unlike _pending) + an OR of every chunk's / the EndOfTask's partial flag.
        self._count: dict[str, int] = {}
        self._partial: dict[str, bool] = {}
        # task_id -> "<ExcType>: <msg>" recorded when a flush dropped this task's chunks, so
        # finalize can mark the object failed + attach last_error.
        self._task_errors: dict[str, str] = {}
        # job_ids Engine.cancel_job() has told us about (§ mfs job cancel). A single object's
        # chunks can span many flushes; the producer/object-boundary cancellation check
        # (Engine._should_stop) only stops the NEXT object from starting, so a large single
        # object already mid-flight here would otherwise keep burning embed CPU until it
        # finishes regardless of cancellation. Checked once per flush (not per chunk), so this
        # can't slow down the common (nothing-cancelled) path. Never explicitly pruned: job ids
        # are unique per sync and cancellation is a rare, human-triggered action, so this can't
        # meaningfully grow over a process's lifetime.
        self._cancelled_jobs: set[str] = set()

        self._on_succeeded: list[SuccessCallback] = []
        self._q: Optional[asyncio.Queue] = None
        self._task: Optional[asyncio.Task] = None

    # --- registration ---
    def register_on_succeeded(self, callback: SuccessCallback) -> None:
        """Add a per-task finalize hook, invoked as callback(task_uri, job_id, chunk_count,
        partial, error) when a task reaches a terminal state — either all its chunks were
        written + its EndOfTask was seen (error is None), or a flush dropped its chunks (error
        carries the exception). Job Lane completion §6.4.4; objects-table update.
        Callbacks may ignore the trailing args."""
        self._on_succeeded.append(callback)

    # --- lifecycle ---
    def start(self, chunks_q: asyncio.Queue) -> Task | None:
        """Spawn run() as a background task and remember it for shutdown()."""
        self._q = chunks_q
        self._task = asyncio.create_task(self.run(chunks_q))
        return self._task

    async def run(self, chunks_q: asyncio.Queue) -> None:
        """Consume loop: accumulate chunks, flush on batch_size or idle, finalize tasks.

        Every unit of work (idle flush, consume) is guarded: a transient failure (DB hiccup
        in a callback, a Milvus upsert error) is logged and the loop keeps draining rather
        than dying. A dead consumer task would wedge every later chunk, so resilience here is
        a hard requirement (§5.2)."""
        self._q = chunks_q
        idle_s = self._idle_ms / 1000.0
        while True:
            try:
                item = await asyncio.wait_for(chunks_q.get(), timeout=idle_s)
            except asyncio.TimeoutError:
                await self._safe_flush()  # idle: write what we have so small jobs don't stall
                continue
            if item is _SHUTDOWN:
                await self._safe_flush()
                break
            try:
                await self._consume(item)
            except Exception as e:  # noqa: BLE001 — never let one bad item kill the consumer
                logger.warning("embed consumer consume failed: %s", e)

    async def _safe_flush(self) -> None:
        """_flush that logs (never raises) — for the loop's idle/shutdown drains, so a failed
        write doesn't terminate the consumer. _flush itself preserves the batch on failure, so
        the next flush retries it (nothing is lost)."""
        try:
            await self._flush()
        except Exception as e:  # noqa: BLE001
            logger.warning("embed consumer flush failed: %s", e)

    async def shutdown(self) -> None:
        """Signal the loop to drain its pending batch and stop; await the run task."""
        if self._q is not None:
            await self._q.put(_SHUTDOWN)
        if self._task is not None:
            await self._task

    # --- retry ---
    def on_task_retry(self, task_id: str) -> None:
        """Drop ALL per-task state before the engine re-pumps a task's producer (§6.1).

        A producer that raised mid-stream left partial state behind: chunks already in the
        batch, an inflated _pending/_count, and `task_id` in _deleted (so the next first-chunk
        would SKIP delete_by_object). Carrying any of that into the retry would double-count
        the chunk_count, leave stale chunks the retry no longer produces, and corrupt the
        pending counter. Resetting to fresh state makes the retry behave like a first attempt:
        delete_by_object runs again and the counters reflect only the retry's chunks."""
        self._batch = [(e, c) for (e, c) in self._batch if e.task_id != task_id]
        self._pending.pop(task_id, None)
        self._count.pop(task_id, None)
        self._partial.pop(task_id, None)
        self._eot.pop(task_id, None)
        self._deleted.discard(task_id)
        self._meta.pop(task_id, None)
        self._task_errors.pop(task_id, None)

    # --- cancellation ---
    def mark_job_cancelled(self, job_id: str) -> None:
        """Record a job as cancelled (called from Engine.cancel_job()) so the NEXT flush
        skips embedding its already-queued chunks instead of spending real embed-API/CPU
        time on work whose result will never be written. This can't make an in-flight
        batch_embed() call return early -- that one batch still runs to completion -- but
        it stops every batch after it, which is the realistic bound on how fast `mfs job
        cancel` can actually take effect for a single large object still mid-flight."""
        self._cancelled_jobs.add(job_id)

    # --- consume ---
    async def _consume(self, env: TaskEnvelope) -> None:
        payload = env.payload
        if isinstance(payload, EndOfTask):
            await self._handle_eot(env)
        else:
            await self._handle_chunk(env, payload)

    async def _handle_chunk(self, env: TaskEnvelope, chunk: Chunk) -> None:
        tid = env.task_id
        self._meta[tid] = env
        # per-object atomic: purge stale chunks once, on this task's FIRST chunk, before
        # any upsert lands (§6.1). Idempotent + safe to retry (delete-by-filter no-ops
        # when nothing matches), so re-running a task just deletes again.
        if tid not in self._deleted:
            await self._milvus.delete_by_object(env.connector_uri, env.task_uri)
            self._deleted.add(tid)
        self._pending[tid] = self._pending.get(tid, 0) + 1
        if chunk.partial:
            self._partial[tid] = True
        self._batch.append((env, chunk))
        if len(self._batch) >= self._batch_size:
            await self._flush()

    async def _handle_eot(self, env: TaskEnvelope) -> None:
        tid = env.task_id
        self._meta[tid] = env
        self._eot[tid] = env.payload.partial
        if env.payload.partial:
            self._partial[tid] = True
        if self._pending.get(tid, 0) == 0:
            # all chunks already written (or this task produced none) — a zero-chunk
            # rebuild still must purge any prior index (§6.1).
            if tid not in self._deleted:
                await self._milvus.delete_by_object(env.connector_uri, env.task_uri)
                self._deleted.add(tid)
            await self._maybe_finalize(tid)

    # --- flush ---
    async def _flush(self) -> None:
        if not self._batch:
            return
        # No concurrent appender can grow it mid-flush: _flush only runs inside the single
        # run() loop, so this snapshot IS the whole pending batch. Claiming it (rather than
        # just aliasing it) up front is safe for the same reason: nothing else touches
        # self._batch while this coroutine is suspended on an await below.
        batch = self._batch
        self._batch = []
        if self._cancelled_jobs:
            cancelled = [item for item in batch if item[0].job_id in self._cancelled_jobs]
            if cancelled:
                batch = [item for item in batch if item[0].job_id not in self._cancelled_jobs]
                # Reuse the existing failure path rather than inventing new bookkeeping --
                # cancellation was already called out as one of _fail_batch's intended
                # triggers ("an OOM embed, a Milvus error, a cancellation") when that method
                # was written; this is the first caller to actually exercise it that way.
                await self._fail_batch(cancelled, RuntimeError("job_cancelled"))
                if not batch:
                    return
        try:
            # 1. tx_cache lookup for vectors, embed only the misses (§6.3), cache them back.
            keys = [self._cache_key(ch) for _, ch in batch]
            cached = await self._tx_cache.batch_get(keys) or {}
            miss_idx = [i for i, k in enumerate(keys) if cached.get(k) is None]
            if miss_idx:
                new_vecs = await self._embedder.batch_embed([batch[i][1].content for i in miss_idx])
                put: dict[str, list[float]] = {}
                for j, i in enumerate(miss_idx):
                    cached[keys[i]] = new_vecs[j]
                    put[keys[i]] = new_vecs[j]
                await self._tx_cache.batch_put(put)

            # 2. one upsert for the whole (cross-task, cross-kind) batch — idempotent by
            #    chunk_id PK (§5.3 / §6.2). delete_by_object already ran per task on receipt.
            built = [
                (env.task_id, self._build_row(env, ch, cached[keys[i]]))
                for i, (env, ch) in enumerate(batch)
            ]
            # de-dupe by chunk_id (last-occurrence wins) so two chunks colliding on the same
            # locator-derived id within one batch don't make Milvus reject the WHOLE batch —
            # rows without a chunk_id (the base class's default _build_row, used directly in
            # unit tests) skip this, since there is nothing to collide on.
            if built and "chunk_id" in built[0][1]:
                deduped: dict[str, tuple[str, dict]] = {}
                for tid, row in built:
                    deduped[row["chunk_id"]] = (tid, row)
                built = list(deduped.values())
            rows = [row for _, row in built]
            # raw per-task counts across the WHOLE flush attempt (every chunk processed, dedup
            # or not) — pending tracks "no longer queued for a future flush", true regardless
            # of whether the row survived dedup or the upsert ultimately succeeds.
            counts: dict[str, int] = {}
            for env, _ in batch:
                counts[env.task_id] = counts.get(env.task_id, 0) + 1
            await self._milvus.upsert(rows)
        except BaseException as exc:  # noqa: BLE001
            # Agnostic failure handling: we do NOT inspect the exception type or message, do NOT
            # retry, and do NOT split the batch. Any failure (an OOM embed, a Milvus error, a
            # cancellation) structurally releases this batch's per-task bookkeeping so the tasks
            # finalize as failed and the run() loop keeps draining — the alternative is a wedged
            # consumer where pending never reaches zero and the task is stuck 'running' forever.
            await self._fail_batch(batch, exc)
            return

        # 3. write acked: clear the batch, credit chunk_count for what actually persisted
        # (the deduped rows — not the raw per-task counts above), then decrement pending and
        # finalize.
        self._batch = []
        written: dict[str, int] = {}
        for tid, _ in built:
            written[tid] = written.get(tid, 0) + 1
        for tid, n in written.items():
            self._count[tid] = self._count.get(tid, 0) + n
        for tid, n in counts.items():
            self._pending[tid] = self._pending.get(tid, 0) - n
            await self._maybe_finalize(tid)

    async def _fail_batch(
        self, batch: list[tuple[TaskEnvelope, Chunk]], exc: BaseException
    ) -> None:
        """Drop a failed flush batch and release each task's bookkeeping so it can finalize."""
        affected: list[str] = []
        seen: set[str] = set()
        for env, _ in batch:
            if env.task_id not in seen:
                seen.add(env.task_id)
                affected.append(env.task_id)
        logger.warning(
            "embed flush failed for %d chunk(s) across %d task(s); dropping the batch "
            "and finalizing them as failed: %r",
            len(batch),
            len(affected),
            exc,
        )
        # The whole batch is discarded (these chunks are NOT written). Decrement each chunk's
        # pending (clamped at 0 — never negative), flag the task partial, and record the raw
        # error so finalize can attach it as last_error.
        self._batch = []
        msg = f"{type(exc).__name__}: {exc}"
        for env, _ in batch:
            tid = env.task_id
            self._pending[tid] = max(0, self._pending.get(tid, 0) - 1)
            self._partial[tid] = True
            self._task_errors.setdefault(tid, msg)
        for tid in affected:
            await self._maybe_finalize(tid)

    # --- finalize ---
    async def _maybe_finalize(self, task_id: str) -> None:
        if self._pending.get(task_id, 0) <= 0 and task_id in self._eot:
            env = self._meta[task_id]
            chunk_count = self._count.get(task_id, 0)
            partial = self._partial.get(task_id, False)
            error = self._task_errors.get(task_id)
            await self._fire_success(env.task_uri, env.job_id, chunk_count, partial, error)
            self._cleanup(task_id)

    async def _fire_success(
        self,
        task_uri: str,
        job_id: Optional[str],
        chunk_count: int,
        partial: bool,
        error: Optional[str] = None,
    ) -> None:
        # Each callback is isolated: a DB hiccup in one hook (e.g. the objects-table writer)
        # must not skip the others or bubble up and kill the consumer. Log and continue.
        for cb in self._on_succeeded:
            try:
                res = cb(task_uri, job_id, chunk_count, partial, error)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:  # noqa: BLE001
                logger.warning("embed success hook failed for %s: %s", task_uri, e)

    def _cleanup(self, task_id: str) -> None:
        self._pending.pop(task_id, None)
        self._eot.pop(task_id, None)
        self._deleted.discard(task_id)
        self._meta.pop(task_id, None)
        self._count.pop(task_id, None)
        self._partial.pop(task_id, None)
        self._task_errors.pop(task_id, None)

    # --- overridable hooks (the adapters in adapters.py supply the real ones) ---
    def _cache_key(self, chunk: Chunk) -> str:
        """Per-input embed cache key (§6.3). Default hashes content only; the adapter
        overrides to fold in provider + model + version so a model swap re-embeds."""
        return hashlib.sha1(chunk.content.encode("utf-8")).hexdigest()

    def _build_row(self, env: TaskEnvelope, chunk: Chunk, vec: list[float]) -> dict:
        """Map a Chunk + its vector to a Milvus row. The adapter overrides to add
        chunk_id (PK) + namespace; here we keep the transport-level fields."""
        content, _ = cap_content(chunk.content)
        return {
            "object_uri": env.task_uri,
            "connector_uri": env.connector_uri,
            "connector_job_id": env.job_id,
            "chunk_kind": chunk.chunk_kind,
            "locator": chunk.locator,
            "content": content,
            "dense_vec": vec,
            "metadata": chunk.metadata,
            "partial": chunk.partial,
        }
