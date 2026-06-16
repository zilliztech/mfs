# Built for production

Good retrieval quality is the easy part. The hard part of a context layer is
*running it every day*: a sync killed halfway, two processes touching the same
store, an index cleared down to a stub, one malformed file taking out a whole
run. Those are state, lifecycle, and concurrency problems — orthogonal to how
good your search is — and they're where most "thin search engine" tools quietly
break under real traffic.

MFS is engineered so these are **structural non-issues** rather than bugs you hit
and patch one by one. The same design that lets it run on a laptop is what lets
it run in production.

| The hard part of running this daily | How MFS handles it |
|---|---|
| A sync killed halfway | Per-object atomic writes; the next `mfs add` just resumes |
| Two processes touching the same store | One server owns all state and the index connection; clients talk HTTP |
| A re-run duplicating results | Idempotent, namespace + connector-keyed records — re-runs overwrite |
| A partial ingest deleting the wrong things | Deletions happen only on a full-set scan; incremental never infers them |
| One bad file breaking the whole run | Per-object failure isolation plus a circuit breaker |
| Config from one source leaking into another | Per-connector config is data in the DB, not shared process state |

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
[How it works](architecture.md) for the components and [Deployment](deployment.md)
for the topologies.
