# Design philosophy

This page is the *why* behind the [Architecture](architecture.md): the design
decisions that make MFS hold up in daily use, and the failure modes each one
rules out.

Good retrieval quality is the easy part. The hard part of a context layer is
*running it every day*: a sync killed halfway, two processes touching the same
store, an index cleared down to a stub, one malformed file taking out a whole
run. Those are state, lifecycle, and concurrency problems — orthogonal to how
good your search is — and they're where most "thin search engine" tools quietly
break under real traffic.

MFS is engineered so these are **structural non-issues** rather than bugs you hit
and patch one by one. The same design that lets it run on a laptop is what lets
it run in production.

| A concrete failure mode | Why it doesn't happen in MFS |
|---|---|
| Indexing is killed at file 8,000 of 20,000; on restart the tool either re-indexes the whole repo from scratch or loops forever thinking nothing is done | Work is per object and committed as it goes; the next `mfs add` picks up at file 8,001 |
| A background file-watcher holds a lock on the local vector database, so a `search` you run in another terminal can't even open it | Only the server ever opens the vector store; every client goes over HTTP, so there's no file for a second process to lock |
| Two editor windows each auto-start a sync of the same repo and trample each other's writes | At most one sync runs per connector (plus one queued); the duplicate is refused, not run twice |
| You re-index just the 50 files that changed, and the tool deletes the other 19,950 because they weren't in this batch | Deletions happen only on a full-set scan that enumerated the whole source; a partial run never removes what it didn't list |
| One file with a broken encoding throws, and the entire indexing run dies with it | Each object succeeds or fails on its own; the bad one is marked `failed` and skipped, the rest finish |
| An ignore rule or API key you set for one repo silently applies to the next repo in the same session | Each connector's config is isolated rows in the database, not shared state in a long-lived process |
| Switching the embedding model means manually deleting the index, deleting a snapshot file, editing `.env`, and rebuilding from zero | The index is derived — `mfs add --force-index` rebuilds it from source, and the config lives with the connector |

## One source of truth

Your upstream sources are the truth. MFS's metadata database is only its
*knowledge* of them; the Milvus index and the caches are **derived**. Delete the
index and rebuild it from the original sources and you lose nothing.

Clients hold almost no state — even the file manifest for an upload lives in the
server's `file_state` table, not in a client-side snapshot. There's no local
snapshot file that can drift out of sync with what's actually indexed, which is a
whole class of "the snapshot says 0 files, reindex forever" bugs that simply
can't be expressed here.

## A stateful server arbitrates everything

MFS is a thin client over one stateful server. The **server alone** holds the
connection to Milvus and owns all state; every client — CLI, SDK, agent skill —
talks to it over HTTP. So multiple CLIs or agent sessions never contend over a
database file or leak state into one another. The classic embedded-database
failure — one process locks the vector file and another can't even open it — has
nowhere to occur, because only the server ever touches it.

## Jobs with a real lifecycle

Ingest runs as a job in a database-backed queue, not as fire-and-forget work. A
uniqueness rule keeps at most one running and one queued sync per connector, so
hitting `mfs add` twice doesn't double-scan or collide — the second is told the
sync is already running. Sync is **explicit**: you trigger it. There's no hidden
background scheduler racing itself or re-scanning behind your back.

## Crash-safe and resumable

Work is per-object and atomic, and a run's state is committed at the end. If the
process is killed mid-sync, nothing is left half-committed — the next `mfs add`
picks up where it left off. Recovery collapses to "just run it again", because
every write is **idempotent**: index records are keyed by namespace and
connector, so a re-run overwrites instead of duplicating, and two different
sources can never collide on the same key.

## Incremental, and careful about deletions

Re-syncing is incremental: per-object fingerprints (and connector cursors) mean
only what actually changed is re-converted and re-embedded; everything unchanged
is served from cache. Deletion is deliberately conservative — an incremental run
**never infers deletions**. Only a full-set scan, which has truly enumerated the
whole source, diffs it and removes what's genuinely gone. A partial or batched
ingest therefore can't accidentally delete the records that weren't in this
batch.

## Failures stay contained

A worker pool drains jobs with timeouts and a circuit breaker. A single
malformed object is marked failed and skipped rather than crashing the run; the
breaker aborts a job only when failures pile up, surfaced as a clear code instead
of a silent hang. A job that gets stuck times out and resets instead of wedging
the queue forever.

## Isolation between sources

Per-connector configuration is stored as data in the database, not as mutable
state shared inside a long-lived process. Two projects or sources can't leak
settings — ignore rules, file extensions, credentials — into each other.

---

These properties are exactly what lets the same MFS move from a laptop to
production by configuration alone: point the vector backend at a managed
Milvus/Zilliz cluster and metadata at Postgres, and the same crash-safe,
concurrency-safe, idempotent design handles large corpora and real traffic. See
[Architecture](architecture.md) for the components and [Deployment](deployment.md)
for the topologies.
