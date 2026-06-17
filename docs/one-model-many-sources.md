# One model, many sources

A code repo, a Postgres table, a Slack workspace, a folder of PDFs — they share
almost nothing:

| Source | What one "thing" is | How you reach it | How it signals change |
|---|---|---|---|
| a code repo | a file | read the disk | the file's content hash |
| a Postgres table | a row | run a SQL query | an `updated_at` column |
| a Slack channel | a message thread | call the Slack API | the timestamp of the newest message |
| a folder of PDFs | a file, converted to text | read, then convert | size + mtime, then a hash |

Left alone, each would need its own indexer and its own search stack. MFS's bet is
to **project every source onto one small model** before it reaches the rest of the
system — so search, browse, caching, and recovery are written once and never need
to know what the source really was. The rest of this page is those projections,
and the few places where MFS deliberately lets sources differ.

## Projection 1 — everything is a file-like tree

Whatever the source, you walk it with the same `ls` / `cat` / `tree` you'd use on
a folder. A database and a chat workspace browse identically:

```bash
$ mfs ls postgres://prod/public
tickets/   users/

$ mfs ls slack://acme/channels
eng-backend__C012345/   general__C067890/
```

"File-like" here means *addressable and readable*, not *stored as a file*: a
table's rows are readable, a channel is listable — that's enough. Objects even
carry a type hint in their name, so one `cat` knows how to render each:

| You see | It is | `cat` renders |
|---|---|---|
| `…/tickets/rows.jsonl` | a table's rows | the records |
| `…/tickets/schema.json` | the table's shape | the columns |
| `…/pages/install.md` | a converted web / Notion page | Markdown |

## Projection 2 — a handful of kinds

Under the tree, every object is sorted into one of a **small, fixed set of
kinds**, and the kind — not the source — decides what becomes searchable:

| Example object | Kind | What it becomes |
|---|---|---|
| `engine.py` | code | line-addressed chunks |
| a design PDF | document | converted text, split into chunks |
| one ticket row | table row | one searchable record |
| a Slack thread | message thread | one chunk per thread |
| a screenshot | image | a VLM description (when enabled) |

The variety of the world's data is huge; the variety of *what you do to make it
searchable* is small. Pin each object to a kind and the expensive machinery —
chunking, embedding, indexing — is built once and inherited by every source. A new
source is **classified**, not engineered.

## Projection 3 — one handle reopens any hit

A search result is only useful if you can reopen the exact thing it found, whether
that's some lines in a file or one row in a table. So every hit comes back with a
`source` (which object) and a `locator` (which slice inside it), and the *same*
`cat` reopens either:

```bash
# hit in a code file → the locator is a line range
mfs cat file://local/repo/engine.py --locator '{"lines":[42,78]}'

# hit in a database table → the locator is the row key
mfs cat postgres://prod/public/tickets/rows.jsonl --locator '{"id":12345}'
```

You never learn a per-source addressing scheme. A hit from anywhere is something
you reopen with the same command.

## Projection 4 — many change stories, one report

Detecting change is where sources differ most, so MFS doesn't unify the
*detection* — it unifies the *report*. Each family works out what moved in its own
way, then reports the same three things back: added, changed, removed.

| Family | An object is | A re-sync detects change by | Example |
|---|---|---|---|
| **Files & blobs** — file, S3, Drive, web, repo code | a file | re-scanning and comparing content hashes | edit `README.md` → its hash changes → re-indexed; delete it → caught by the full-set diff |
| **Databases & warehouses** — Postgres, Mongo, BigQuery… | a table's rows | riding an `updated_at`-style cursor | 12 rows change in a 1M-row table → only those 12 are re-pulled |
| **Messages & mail** — Slack, Gmail, Feishu… | a message stream | advancing past the newest message seen | 30 new messages since last sync → only those fetched; old threads untouched |
| **Issues, CRM & docs** — Jira, Linear, Notion… | records | polling an updated timestamp | a reopened Jira ticket → re-indexed on the next sync |

Because the *report* is identical, re-syncing, incremental updates, and deletion
handling are written once — never against any one source's quirks.

## Where MFS deliberately lets sources differ

Forcing *everything* uniform would be a straitjacket. Two things are left to each
connector — and both are **declared**, so the framework can plan around them
instead of special-casing by name:

| Difference | Example | What MFS does |
|---|---|---|
| **What it can push down** | Postgres can run a `grep` as a SQL `WHERE`; a plain folder can't | uses the source's own filter when it has one, falls back to its own scan when it doesn't |
| **Whether it has a time cursor** | `gdrive` and `feishu` accept `mfs add --since 2026-01-01`; most connectors don't | honors `--since` where it's real, rejects it where it isn't |
| **The tree layout** | a repo mirrors its folders; Postgres lays out `schema/table/rows.jsonl` | each connector designs its own tree; everything *beneath* the layout stays uniform |

## Why the line is drawn here

Search, ranking, the caches, crash recovery, the index — all written against the
one model, so they behave the same for a source shipped today and one shipped next
year. And a new connector is a thin adapter: classify its objects, report changes
in the common vocabulary, lay out a tree. Get that right and the whole engine
comes for free.

It's the same trade that lets a shell drive countless programs through a few file
operations — **unify the common part hard, keep the differences thin.** See
[Design philosophy](production.md), [Architecture](architecture.md#core-concepts),
and [Connectors](connectors.md).
