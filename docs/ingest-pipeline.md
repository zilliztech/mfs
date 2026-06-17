# Ingest Pipeline

This page describes how the server-side engine turns a stream of pending objects
into rows in the vector index. It is the deepest layer of the architecture — for
the system-level map, see [Architecture](architecture.md); for the server's
process and module layout, see [Server](server.md).

The pipeline's shape follows from one question: **at what granularity can a piece
of work be extracted?**

Most indexing is per-object and independent — read one object, split it, embed it
— so it runs in the **Object Lane**, always on. But some work can't be done one
object at a time. A directory summary has to fold its children in order
(bottom-up: a parent only after its sub-directories), so it depends on other
objects instead of belonging to any single one. That runs at job granularity, in
the **Job Lane** (optional, enabled by `[summary].enabled`). The Job Lane is the
general home for that whole class — anything with cross-object ordering or
dependencies — and a directory summary is, today, its one member.

Both lanes end in the same thing, chunks, so they **converge into one embedding
tail**: one chunk queue, one embed consumer, one index. A directory summary lands
as just another chunk kind next to `body`, `row_text`, and the rest. The lanes
run in parallel — the Job Lane folds children's *source content* (re-read or
reused from the cache, not their embeddings), so it doesn't wait for the Object
Lane, and because the two lean on different backends (the embedder vs. the summary
LLM/VLM), running them together keeps both busy.

Both lanes are wired around the same four concepts. The diagrams below mark
them with the same icons:

- 📋 **Metadata** — durable state describing what work exists and what work is
  done (`ObjectTask` table, `Objects` table).
- 🟦 **Queues** — the durable `ObjectTask` queue plus the in-memory `ChunkQueue`
  and `SummaryQueue`.
- 💾 **Caches** — Transformation Cache (content-addressed memoization of model
  outputs) and Artifact Cache (per-object derived blobs, local filesystem).
- 🟩 **Index** — the vector store (Milvus Lite by default, or a configured
  Milvus / Zilliz endpoint).

## High-level overview

![Ingest pipeline overview — two upstream lanes share one cache pair, one chunk queue, one embed consumer, and one index](https://github.com/user-attachments/assets/68d840c1-b510-49c3-82b5-fd610b2871ed)

The two lanes feed into one tail.

- **The Object Lane works at object granularity** — it claims one `ObjectTask`
  (a file, an image, a table-rows batch, a message stream, …) at a time. Each
  task becomes one stream of chunks.
- **The Job Lane works at job granularity** — it builds one in-memory directory
  tree per connector job and folds directories bottom-up. A directory is ready
  to fold once enumeration is complete (for a leaf) and its sub-directories are
  summarized; it does **not** wait for its own files to be embedded. Each ready
  directory becomes one chunk.
- **From `ChunkQueue` onward, everything is shared.** One `ChunkQueue`, one
  `EmbedConsumer`, one cache pair, one index. The Job Lane has no separate embed
  path, no separate upsert path, no separate collection — its output is just
  one more `chunk_kind` (`directory_summary`) alongside `file`, `code`,
  `image`, and the rest.
- **The shared cache pays off across the two lanes.** Folding a directory
  re-reads whatever PDF was converted or whatever image was VLM-described in the
  Object Lane. A conversion is reused from the Artifact Cache when its
  content+version token still matches; a VLM/summary call is memoized in the
  content-addressed Transformation Cache under a single-flight lock. Either way
  the two lanes never re-run the same work, even if they reach the same input at
  the same moment.

The next two sections open each lane.

## Object Lane: object → chunks → vectors

![Object Lane — per-object tasks flow into chunks, embeddings, and the shared index](https://github.com/user-attachments/assets/9c527db1-b0b1-478c-8a04-e4de9287ade8)

Notes:

- **Queue ① is durable.** `ObjectTask` rows sit in the metadata database, so a
  worker crash never loses pending work. Workers claim by `(priority ASC,
  started_at ASC)`.
- **The Producer pool is okind-dispatched.** One `ChunksProducer` per object
  kind (text/code/document, image, message_stream, record_collection,
  table_rows, table_schema). The pool itself does not know what it will get;
  `select_producer(okind, ctx)` picks the implementation. Adding a new kind is
  one new producer file plus one new dispatch branch.
- **Queue ② is in-memory and bounded.** `ChunkQueue` is an `asyncio.Queue` whose
  `maxsize` is derived from `embedding.batch_size`. When the consumer falls
  behind, producers block on `put()`. This is the single most important
  property of the redesign: the upstream cannot outrun the downstream, so the
  in-flight chunk set is hard-bounded — memory cannot grow without limit.
- **`EmbedConsumer` is a process singleton.** It accumulates chunks across
  every task and every okind, flushes on `batch_size` or after an idle timeout,
  calls embed once and upserts once. A small task piggybacks on a large task's
  batch, so embed call density stays high across mixed workloads.
- **Per-object atomicity.** When a task's first chunk arrives, the consumer
  issues `delete_by_object` for that object's stale rows before any new upsert
  lands. The index never holds an overlap between the old and new versions of
  the same object. Re-running a task is safe because `delete_by_object` is
  idempotent.
- **The success hook closes the loop.** When every chunk for a task is written
  and its `EndOfTask` has been seen, the consumer fires registered hooks: one
  flips the `ObjectTask` row to `succeeded` and writes the `Objects` row;
  another advances the Job Lane's completion count when the task was a persisted
  `directory_summary`. Failure in a flush is also delivered through the hook,
  with an error string, so the task can be marked failed.

## Job Lane: directory summaries

![Job Lane — bottom-up directory summaries enter the same embedding and index tail](https://github.com/user-attachments/assets/19389954-5167-44bd-9406-44e662045e82)

Notes:

- **`DirTreeBuilder` is in-memory state.** Unlike the durable `ObjectTask`
  queue, the tree is built during connector sync and lost on crash. Crash
  recovery reconstructs it from durable object state.
- **Files do not gate a directory.** A summary folds its children's source
  content, not their embeddings, so a directory's `pending` counts only its
  un-summarized sub-directories. At sync end every leaf directory is ready
  immediately; the two lanes then run in parallel — a directory can be
  summarized while its files are still being embedded by the Object Lane.
- **Bottom-up ordering.** `SummaryQueue` is a per-job heap keyed on negative
  depth, so the deepest ready directory pops first. A cross-job round-robin
  dispatcher fans the per-job heaps into a single `ready_q`, so one large
  deep job cannot starve another job's shallow tree.
- **Cache reuse pays off here.** Folding a directory's children reuses whatever
  was already converted or VLM-described in the Object Lane. A conversion is
  reused from the Artifact Cache when its content+version token matches (else it
  is converted once and cached for both lanes); a VLM/summary call is memoized
  in the Transformation Cache under a single-flight lock. The two lanes never
  re-run the same work for the same input, even if they reach it concurrently.
- **The Job Lane shares the tail.** Every `directory_summary` chunk is written
  into the same `ChunkQueue`. There is no separate embed path, no separate upsert
  path, no separate index. From the `EmbedConsumer`'s perspective,
  `directory_summary` is just one more `chunk_kind`, sitting next to `file`,
  `code`, `image`, and the rest in the same Milvus collection.
- **Shared provider budgets.** The VLM and summary providers are protected by
  process-wide concurrency gates (`description_gate`, `summary_gate`) that both
  lanes share. The Job Lane folding an image draws from the same in-flight VLM
  budget as the Object Lane image producer, so enabling summaries does not
  double the pressure on the provider.

## Why this shape

The design follows from a few first principles, not from any single feature.

### Two lanes, because granularity differs

The split is by the one thing that actually varies: **the granularity at which
work can be extracted.** If a piece of work can be done one object at a time, with
no dependence on other objects, it takes the Object Lane. If it can't — because it
needs an order, or it folds several objects together — it takes the Job Lane. A
directory summary is the standing example: a parent can only be summarized after
its children, so the work is inherently job-level, not object-level. The lane is
the general home for that class, so future cross-object work (other rollups,
dependency-ordered derivations) joins it without a new pipeline. Both lanes end in
chunks, so they converge into one embedding tail.

### Two queues, to decouple producer from consumer

A queue between stages lets each side run at its own pace: producers pile work up,
and when the queue fills they **block** (backpressure) and wait their turn rather
than running the server out of memory. MFS uses two queues for two different jobs,
and they're built differently on purpose.

**The upstream queue is durable** — the `ObjectTask` table. Its job is to record
*what state each object is in*: done, failed, or not yet processed. That record is
the whole point. A second `mfs add`, a cancel, or a duplicate request is resolved
against it; and because it survives a crash, a restart simply resumes. This is
what makes the pipeline idempotent and recoverable — re-runs, duplicates, and
"index, then cancel" are all settled here, at the durable front of the line.

**The downstream queue is in-memory** — the `ChunkQueue`. An embed consumer pulls
the already-split chunks from it and embeds them in batches. Putting a queue in
front of that consumer is what lets the **batch size be tuned freely**, and that
matters because **embedding is the slowest step in the system — the bottleneck —**
and different embedding services have very different throughput, so it must be the
operator's to configure. This queue doesn't need to be durable: splitting an
object into chunks is fast and cheap, so if the in-memory queue is lost in a
crash, the durable upstream queue just replays those objects and the chunks are
remade in moments.

### One tail, so every kind shares one path

Because both lanes converge on the same `ChunkQueue` → embed consumer → index,
there is exactly one embedding path and one upsert path. A new chunk kind — a
file-level summary, a table rollup — is just another value flowing through it, with
no second embed path and no second collection. And since the consumer batches
across every task and kind at once, a small task rides along in a large one's
batch, keeping that expensive embed call dense.
