# Design philosophy

This page is the *why* behind the [Architecture](architecture.md): the design
decisions behind MFS and what each one buys you. A couple are about product shape
— who it's for, how you extend it. Most are about the unglamorous part: holding
up when you actually run it every day.

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
picks up where it left off.

Recovery collapses to "just run it again", because every write is **idempotent**.
A chunk's primary key *is* its content address —
`sha1(namespace + connector + object + chunk kind + locator)` — so writing it is a
delete-then-insert that any worker, retry, or concurrent run produces identically:
a re-run overwrites instead of duplicating, and two sources can never collide on
the same key. That's why there's no `job retry` command, no resume-cursor state
machine, no "how far did I get" bookkeeping — crashed means re-run, and the result
is the same.

This is also the backstop under several other decisions: you can delete and
rebuild the index freely without corrupting it, the caches can be best-effort
(lose them and they recompute), and a flaky run can simply abort and be re-run.
All of it rests on "doing it twice is harmless".

## Incremental, and careful about deletions

Re-syncing is incremental: per-object fingerprints (and connector cursors) mean
only what actually changed is re-converted and re-embedded; everything unchanged
is served from cache. Deletion is deliberately conservative — an incremental run
**never infers deletions**. Only a full-set scan, which has truly enumerated the
whole source, diffs it and removes what's genuinely gone. A partial or batched
ingest therefore can't accidentally delete the records that weren't in this
batch.

## Two lines of defense against cost

Two kinds of cost, two independent mechanisms:

- **Bandwidth.** In a client/server setup the file connector keeps a per-path
  manifest (`file_state`), so a re-sync uploads only the bytes that changed. A
  renamed or moved file is matched to its existing entry and re-uses what's
  already on the server — moving a 1 GB file uploads nothing.
- **Model spend.** The transformation cache memoizes every embedding, VLM, and
  summary call, keyed by the content hash *and* the model. Identical input plus
  identical model is a hit — across objects, across connectors, even across a
  Milvus collection rebuild or an embedding-model rollback, because the cache is
  addressed by content and model, not by anything in the index.

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

## Agent-first, one protocol

MFS's first user is an agent, not a person. The primary interface is a
shell-native CLI built from verbs an agent already knows (`ls`, `cat`, `grep`,
`tree`, …) rather than a new query language, and it ships as a skill so an agent
arrives with the right mental model instead of trial and error.

The CLI, the SDKs, and the skills are all clients of the same HTTP `/v1` — none
has a privileged path. That keeps the three entry points behaving identically and
means adding an SDK never changes how anything else works; the SDKs are the
fallback for programs that can't comfortably shell out, not a second API.

## Built for community

The architecture's central tension is *unify the common parts, isolate the
differences* — and the difference is kept deliberately thin. Everything hard is
the framework's job: chunking, embedding, summaries, VLM, the artifact and
transformation caches, the Milvus schema, retrieval, the HTTP API, the job queue,
fingerprinting, and deletion logic. A contributor writes one connector plugin —
six required methods (`stat` / `list` / `read` / `fingerprint` / `sync` /
`object_kind_of`), a few hundred lines — that just connects the source, lays out
its URI tree, and reports changes. In return it gets the whole chunk → embed →
search → cache → store pipeline for free.

That line is drawn on purpose: a new data source is a plugin, not a fork of the
framework. The aim is for the connector catalog to grow the way Airbyte's or
Singer's did — through the community.

---

These properties are exactly what lets the same MFS move from a laptop to
production by configuration alone: point the vector backend at a managed
Milvus/Zilliz cluster and metadata at Postgres, and the same crash-safe,
concurrency-safe, idempotent design handles large corpora and real traffic. See
[Architecture](architecture.md) for the components and [Deployment](deployment.md)
for the topologies.
