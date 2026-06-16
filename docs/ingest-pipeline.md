# Ingest Pipeline

This page describes how the server-side engine turns a stream of pending objects
into rows in the vector index. It is the deepest layer of the architecture — for
the system-level map, see [Architecture](architecture.md); for the server's
process and module layout, see [Server](server.md).

The pipeline has two lanes, distinguished by the granularity of the work they
collect. They run **in parallel** and feed one shared tail:

- **Object Lane** — always on, works at object granularity. Every object becomes
  a stream of chunks, the chunks become vectors, the vectors become rows in the
  index.
- **Job Lane** — optional, controlled by `[summary].enabled`, works at job
  granularity. It folds each directory of a connector job into a summary,
  bottom-up. Each summary travels back through the same tail and lands in the
  same index as one more chunk kind.

The lanes are not phases: the Job Lane does not wait for the Object Lane to
finish. A directory summary folds its children's **source content** (re-read or
reused from the cache), not their embeddings, so the moment enumeration
completes the Job Lane can summarize a directory while its files are still being
embedded. The two lanes hit different backend services (the embedder vs. the
LLM/VLM), so running them together keeps both busy.

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

## Why this shape

Three pressures push the design into the shape on the next page.

1. **Workloads are heterogeneous and bursty.** Different connectors emit very
   different chunk volumes — a Postgres table can produce millions of small
   row-records, a single PDF only tens of structured chunks, a Slack channel
   sits somewhere in between. The pipeline must not let the chunkiest source
   blow up memory.
2. **The expensive steps are external.** Embedding, VLM description, summary
   generation. Per-call latency and per-call cost are high; both drop sharply
   with batch size. The pipeline must aim for the densest possible batches
   even when many small tasks run side-by-side.
3. **Crashes happen mid-stream.** A worker restart must not duplicate index
   rows, double-charge the provider, or silently skip work that was almost
   done.

The shape that falls out:

| Concern | Mechanism |
|---|---|
| **Memory ceiling** | A bounded shared queue (`ChunkQueue`) makes producers block when the consumer falls behind. The in-flight chunk set is hard-bounded regardless of how chunky the source is. |
| **Batch density** | One process-wide `EmbedConsumer` accumulates chunks across every task and every object kind, then flushes one embed call + one upsert per batch. Small tasks piggyback on large tasks' batches. |
| **Per-object correctness** | The consumer issues `delete_by_object` on a task's first chunk, before any new upsert lands. Re-running a task is idempotent; the index never holds an overlap between old and new versions of the same object. |
| **Extensible to new object kinds** | A typed `ChunksProducer` interface per okind. Adding a new kind (audio, video, …) is one new producer file plus one dispatch entry — no change to the consumer, the cache, or the index path. |
| **Extensible to new aggregate kinds** | The Job Lane emits its output back into `ChunkQueue` as a new `chunk_kind`. Future aggregate types — file-level summaries, table-level summaries, project READMEs — follow the same pattern, with no second embed path. |
| **Crash recovery** | `ObjectTask` is durable. The Job Lane's `DirTreeBuilder` is in-memory but rebuilt from durable object state on restart. |
| **Provider cost** | Model outputs (VLM, summary) are memoized in the content-addressed, single-flight Transformation Cache, and file conversions in the per-object Artifact Cache keyed by a content+version token — so the two lanes never re-run the same conversion or double-call the same provider for the same input, even when they miss concurrently. |

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
