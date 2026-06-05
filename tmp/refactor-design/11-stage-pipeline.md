# Staged Ingest Pipeline (V0.4)

This document describes the MFS ingest engine after the V0.4 refactor: a streaming,
back-pressured pipeline that replaces the old per-object inline `read → chunk → embed →
upsert` loop. It is the public design summary; the day-to-day implementation plan lives
elsewhere.

## 1. Why

The previous engine processed each object inline, holding four buffers alive at once —
the full text, all chunk pairs, all embedding vectors, and the rows to upsert — until the
object finished. Peak memory scaled with object size and multiplied by worker concurrency,
so a single large log file or a busy Slack channel could exhaust a small host. The fix is
to stream: produce chunks one at a time, embed them in cross-object batches, and write to
Milvus continuously, so only a bounded amount of work is ever in flight.

## 2. Concepts

User-facing:

- **Connector** — a registered data source instance (`postgres://prod`, `./repo`).
- **Object** — one virtual file a connector exposes (a URI + media type); what `ls`,
  `cat`, and `search` operate on.
- **Chunk** — one Milvus row; the smallest unit `search` / `grep` can recall.
- **ConnectorJob** — one sync invocation (`mfs add`, `mfs connector update`).

Engine-internal:

- **ObjectTask** — a per-object work unit of a ConnectorJob (a row in `object_tasks`).
- **ChunksProducer** — a per-object-kind strategy that turns one ObjectTask into a stream
  of Chunks.
- **EmbedConsumer** — a process-wide singleton that accumulates Chunks across all jobs,
  embeds them in batches, and writes Milvus.
- **DirTreeBuilder / SummaryQueue / SummaryWorker** — the Reduce subsystem that produces
  directory summaries bottom-up.

## 3. Two subsystems, one queue

Each ConnectorJob forks into two independent subsystems that converge on a single queue:

```
 sync() ──┬─► Map subsystem  (object_tasks table, unordered/parallel)
          │      ChunksProducer pool ─┐
          │                           ├─► chunks_q ─► EmbedConsumer ─► Milvus
          └─► Reduce subsystem (in-memory dir tree, bottom-up)
                 SummaryWorker pool ──┘
```

- **Map** handles work that is self-contained per object: read, convert, describe, chunk,
  embed. Map tasks are persisted in `object_tasks`, claimed globally, and run in any order.
- **Reduce** handles work that folds a directory's children — directory summaries — which
  have a DAG dependency (sub-directories before parents) and a strict bottom-up order. It
  is in-memory, not in `object_tasks`.
- **chunks_q** is the one bounded queue. Both subsystems push Chunks into it; the
  EmbedConsumer does not care where a Chunk came from.

Keeping Map and Reduce on separate schedulers reflects their different shapes: Map is
embarrassingly parallel; Reduce needs ordering and dependency tracking. Forcing both into
one table would require simulating Reduce with priority hacks.

## 4. Map subsystem

A process-global pool of producer coroutines claims pending ObjectTasks from `object_tasks`
ordered by `priority ASC, started_at ASC` — globally, with no per-job filter — so any idle
coroutine immediately picks up the highest-priority pending task regardless of which job it
belongs to. Job concurrency is therefore an emergent property of priority, not a per-job
knob.

Each claimed task is dispatched by object kind to one ChunksProducer:

| Object kind | Producer | Output chunk kind |
|---|---|---|
| `document` / `code` | TextChunksProducer (Chonkie; markdown-aware recursive rules for documents, AST chunker for code) | `body` |
| `image` | ImageChunksProducer (VLM description, concurrency-gated) | `vlm_description` |
| `message_stream` | MessageStreamProducer (materialize to a temp JSONL, regroup by thread) | `thread_aggregate` |
| `table_rows` / `record_collection` | RecordCollectionProducer (per-record streaming) | `row_text` |
| `table_schema` | TableSchemaProducer (LLM schema summary) | `schema_summary` |

A producer reads transformed bytes/records from `plugin.read` / `plugin.read_records`; it
is connector-agnostic, so a new connector that emits an existing object kind needs no new
producer. Image description and summary calls pass through a `ConcurrencyGate` (an
`asyncio.Semaphore` with a business name) that caps in-flight provider calls.

Each producer ends its stream with an `END_OF_TASK` sentinel.

## 5. Reduce subsystem

As `sync()` yields object changes, a per-job **DirTreeBuilder** accumulates an in-memory
tree: each directory node tracks its child files, child sub-directories, and a `pending`
count of children not yet done. No extra DB reads — the object kind is supplied by the sync
loop.

When sync ends the tree is finalized. A directory becomes ready when its `pending` hits
zero, driven by two signals: a child file's Map task succeeding, and a child
sub-directory's summary being computed. Ready directories enter a per-job heap keyed on
`(-depth, time, uri)` so the deepest ready directory pops first (bottom-up). A dispatcher
round-robins across jobs so a deep tree in one job cannot starve a shallow tree in another.

A pool of **SummaryWorker** coroutines drains the ready queue. For each directory a worker
folds its children — file excerpts (pulled from the transformation cache, computed once via
a per-key compute lock if missed) plus its sub-directories' already-computed summaries —
calls the summary model, writes the result back into the node, decrements the parent's
`pending`, and emits a `directory_summary` Chunk into the same `chunks_q`.

Directory summaries are opt-in (`[summary].enabled`); per-file summaries are a separate
opt-in (`[summary].file`, default off).

## 6. EmbedConsumer and per-object atomicity

The single EmbedConsumer accumulates Chunks across all producers and both subsystems, so
embed batches stay full even when individual jobs are small. It flushes when a batch
reaches `[embedding].batch_size` or after a short idle timeout, looks up cached vectors,
embeds the misses, and upserts to Milvus.

Per-object atomicity holds without a transaction: the consumer issues one
`delete_by_object` for an object before its first chunk is written, then upserts the new
chunks (idempotent by `chunk_id` primary key). A per-task pending counter plus the
`END_OF_TASK` sentinel mark a task done; finishing a task both flips its `object_tasks` row
to `succeeded` and notifies the Reduce subsystem's parent directory.

## 7. Caches

Two caches sit beside the data flow as optimizations:

- **transformation_cache** — content-addressed (`sha1(input) + kind + provider + model +
  version`). Deduplicates expensive computation across objects and across the Map/Reduce
  subsystems. A `get_or_compute(key, fn)` with a per-key async lock ensures that when both
  subsystems miss the same hash at once, only one computes it.
- **artifact_cache** — per-object derived products (converted markdown, image description
  text) addressed by object URI, used by `cat` / `head`.

A server restart re-runs a sync at the cost of cache lookups and idempotent re-upserts, with
no provider calls when inputs are unchanged.

## 8. ConnectorJobWatcher

Because no per-job loop owns a job in the new model, a lightweight watcher coroutine polls
`connector_jobs` on a short interval and finalizes jobs out of band: a running job with no
live tasks and a finished Reduce subsystem becomes `succeeded`; a job past the consecutive-
fatal threshold becomes `failed`; a cancelled job has its pending tasks cleaned up. On any
terminal transition it evicts the job's in-memory dir tree.

## 9. Configuration

The TOML schema names each section by the business it controls:

```toml
[chunks_producer]
concurrency = 8

[object_task]
max_retries = 3
consecutive_fatal_threshold = 5

[chunking]
chunk_size = 2048

[embedding]
provider   = "onnx"
batch_size = 100

[description]          # image VLM description
enabled     = false
concurrency = 10

[summary]              # directory / file summaries (Reduce subsystem)
enabled     = false
concurrency = 20
dir         = true
file        = false

[conversion]
default = "markitdown"

[server]
in_process_jobrunner = true
```

Internal constants stay out of TOML: the EmbedConsumer idle-flush timeout and the
`chunks_q` bound (derived from `batch_size`) are source constants, not user knobs.
