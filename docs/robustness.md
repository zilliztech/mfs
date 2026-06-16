# Robustness

Good retrieval quality is the easy part. The hard part of a context layer is
*running it every day*: a sync killed halfway, two processes touching the same
store, an index cleared down to a stub, one bad file taking out a whole run. Those
are state, lifecycle, and concurrency problems — orthogonal to how good your
search is — and they're where most "thin search engine" tools quietly break under
real traffic.

MFS is engineered so these are **structural non-issues** rather than bugs you hit
and patch one by one. It rests on two ideas from the
[Design philosophy](production.md) — the upstream source is the truth, and every
operation is idempotent — and almost everything below follows from them.

| A concrete situation | What MFS does |
|---|---|
| Indexing is killed at file 8,000 of 20,000 | Work is per object and committed as it goes; the next `mfs add` picks up around file 8,001, not from scratch. |
| Your embedding provider runs out of quota halfway and the rest fail | The failed objects are recorded and the cursor isn't moved past them; top up the quota and re-run `mfs add` — it finishes only what's left. |
| You start searching seconds after `mfs add`, before indexing finishes | Indexing is async and incremental: a chunk is searchable the moment its object lands, and the most useful files are done first — so partial answers are useful right away. |
| You cancel a sync (or remove the connector) with thousands of tasks still queued | Cancel and remove also cancel the *queued* tasks, not just the stored rows — the queue doesn't keep grinding through work you stopped. |
| You run the same `mfs add` twice, at once or back to back | At most one sync runs per connector (plus one queued), and every write is idempotent — nothing is processed or stored twice. |
| Some other tool's background watcher locks the local vector database | Only the server ever opens the vector store; every client goes over HTTP, so there's no file for a second process to lock. |
| You re-index just the 50 files that changed | Deletions happen only on a full-set scan; a partial run never removes the 19,950 files it didn't list. |
| One file with a broken encoding throws | Each object succeeds or fails on its own; the bad one is marked `failed` and skipped, the rest finish. |
| You switch git branches back and forth | Unchanged content hits the cache by content hash — nothing is re-embedded; only genuinely changed files cost anything. |
| You rename a directory, so every path under it changes | The chunks are re-keyed and their vectors reused — no re-embedding, even though every path moved. |
| An ignore rule or API key set for one source leaks into another | Each connector's config is isolated rows in the database, not shared process state. |
| You switch the embedding model | The index is derived — `mfs add --force-index` rebuilds it from source; the config lives with the connector. |

The rest of this page is *how*.

## One server owns the state

MFS is a thin client over one stateful server. The **server alone** holds the
connection to Milvus and owns all state; every client — CLI, SDK, agent skill —
talks to it over HTTP. So multiple CLIs or agent sessions never contend over a
database file or leak state into one another. The classic embedded-database
failure — one process locks the vector file and another can't even open it — has
nowhere to occur, because only the server ever touches it.

## A ConnectorJob has a real lifecycle

A sync runs as a **ConnectorJob** in a database-backed queue, not as
fire-and-forget work. A uniqueness rule keeps at most one running and one queued
sync per connector, so hitting `mfs add` twice doesn't double-scan or collide —
the second is told the sync is already running. Sync is **explicit**: you trigger
it. There's no hidden background scheduler racing itself or re-scanning behind
your back. Cancelling a job — or removing the connector — also cancels its queued
ObjectTasks, so a stopped job actually stops instead of draining a queue you no
longer want.

## Crash-safe and resumable

Work is per-object and atomic, and a run's state is committed at the end. If the
process is killed mid-sync — or the embedding provider runs out of quota and the
remaining objects fail — nothing is left half-committed: the failed and unfinished
objects keep their state, and the next `mfs add` picks up only what's left.

Recovery collapses to "just run it again", because every write is **idempotent**.
A chunk's primary key *is* its content address —
`sha1(namespace + connector + object + chunk kind + locator)` — so writing it is a
delete-then-insert that any worker, retry, or concurrent run produces identically:
a re-run overwrites instead of duplicating, and two sources can never collide on
the same key. That's why there's no `job retry` command, no resume-cursor state
machine, no "how far did I get" bookkeeping — crashed means re-run, same result.

## Incremental, and careful about deletions

Re-syncing is incremental: per-object fingerprints (and connector cursors) mean
only what actually changed is re-converted and re-embedded; everything unchanged
is served from cache. Deletion is deliberately conservative — an incremental run
**never infers deletions**. Only a full-set scan, which has truly enumerated the
whole source, diffs it and removes what's genuinely gone. A partial or batched
ingest therefore can't accidentally delete the records it didn't list this time.

## Progressive availability, important things first

Indexing doesn't block search. `mfs add` queues the work and returns; objects are
processed in the background, and each chunk becomes searchable the moment its
object lands — so you can start searching while a large sync is still running, and
`ls --json` shows which objects are `indexed`, `partial`, or `not_indexed` yet.

The order isn't arbitrary. The file connector ranks objects so the highest-signal
ones go first — entrypoints like `README.md` and `CLAUDE.md`, then core source
under `src/` / `lib/`, ahead of tests and generated output. The useful answers
tend to be searchable early, long before the last file is done.

## Reuse instead of recompute

The expensive steps — uploading bytes and calling models — are guarded by two
independent caches, so common edits cost almost nothing:

- **Bandwidth.** In a client/server setup the file connector keeps a per-path
  manifest (`file_state`), so a re-sync uploads only the bytes that changed. A
  renamed or moved file is matched to its existing entry, so moving a 1 GB file
  uploads nothing.
- **Model spend.** The transformation cache memoizes every embedding, VLM, and
  summary call, keyed by the content hash *and* the model. Identical content plus
  identical model is a hit — across objects, across connectors, even across a
  collection rebuild or a model rollback.

Two everyday cases fall straight out of this. **Switching git branches** changes a
few files' content but not the rest, so only the genuinely changed files are
re-embedded. **Renaming a directory** moves every path beneath it, but the
content is identical — MFS re-keys the affected chunks and reuses their vectors
rather than re-embedding the whole subtree the way a path-keyed index would.

## Failures stay contained

A worker pool drains jobs with timeouts and a circuit breaker. A single malformed
object is marked failed and skipped rather than crashing the run; the breaker
aborts a job only when failures pile up, surfaced as a clear code instead of a
silent hang. A job that gets stuck times out and resets instead of wedging the
queue forever.

## Isolation between sources

Per-connector configuration is stored as data in the database, not as mutable
state shared inside a long-lived process. Two projects or sources can't leak
settings — ignore rules, file extensions, credentials — into each other.

---

These properties are what let the same MFS move from a laptop to production by
configuration alone: point the vector backend at a managed Milvus/Zilliz cluster
and metadata at Postgres, and the same crash-safe, concurrency-safe, idempotent
design handles large corpora and real traffic. See [Architecture](architecture.md)
for the components and [Deployment](deployment.md) for the topologies.
