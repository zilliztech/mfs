# One model, many sources

A code repo, a Postgres table, a Slack workspace, a folder of PDFs — they have
almost nothing in common:

| Source | What one "thing" is | How you reach it | How it tells you something changed |
|---|---|---|---|
| a code repo | a file | read the disk | the file's content hash |
| a Postgres table | a row | run a SQL query | an `updated_at` column |
| a Slack channel | a message thread | call the Slack API | the time of the newest message |
| a folder of PDFs | a file, turned into text | read it, then convert it | its size and modified time, then a hash |

If MFS handled each one its own way, every source would need its own indexer and
its own search. Instead, MFS makes **every source look the same to everything
downstream**. Once a source is brought in, search, browse, caching, and recovery
don't know or care what it really was. This page shows the few things MFS makes
identical, the few it lets each source keep its own way, and why that split is
what makes adding a new source easy.

## They all look like a tree of files

Whatever the source, you walk it with the same `ls`, `cat`, and `tree` you'd use
on a folder. A database and a chat workspace browse exactly alike:

```bash
$ mfs ls postgres://prod/public
tickets/   users/

$ mfs ls slack://acme/channels
eng-backend__C012345/   general__C067890/
```

Nothing here is actually a file on disk — a table's rows and a chat's threads
aren't. "Looks like a file" just means you can **list it and read it**, and that's
all the rest of MFS needs. Each object even has a telling extension, so one `cat`
knows how to show it:

| You see | It is | `cat` shows |
|---|---|---|
| `…/tickets/rows.jsonl` | a table's rows | the records |
| `…/tickets/schema.json` | the table's columns | the schema |
| `…/pages/install.md` | a converted web or Notion page | Markdown |

## Every object is one of a few kinds

MFS doesn't try to special-case a thousand formats. It sorts every object into one
of a **small set of kinds**, and the kind — not where it came from — decides how
it's made searchable:

| Example object | Kind | What it turns into |
|---|---|---|
| `engine.py` | code | chunks addressed by line range |
| a design PDF | document | text, converted and split into chunks |
| one ticket row | table row | one searchable record |
| a Slack thread | message thread | one chunk per thread |
| a screenshot | image | a short description (when image support is on) |

There are only a few kinds, so the expensive work behind each one — splitting,
embedding, indexing — is built once and reused by every source. Bringing in a new
source is mostly a matter of saying which kind each thing is.

## Which part of each object gets searched

For a file or a message this is obvious — the file's text, the message's text, all
of it. The interesting case is structured data: a database row has many columns,
and *you* decide which ones are worth searching. You point MFS at three sets of
columns per table:

| Role | Which columns | For a `tickets` table |
|---|---|---|
| The searchable text (what gets embedded) | `text_fields` | `title`, `description` |
| How to reopen the exact row | `locator_fields` | `id` |
| Kept alongside for filtering and display | `metadata_fields` | `status`, `priority`, `updated_at` |

So one ticket row becomes a record built from its title and description,
reopenable by its `id`, with its status and priority carried along. Columns you
don't list — an internal flag, a foreign key — are simply ignored, and a table you
give no `text_fields` still shows up to browse and `grep` but has nothing to search
by meaning.

You don't always have to set this up. The chat and SaaS sources ship sensible
defaults — a Jira issue indexes its summary, description, and comments; a Slack
thread indexes each message as "who: what" — so they work the moment you add them,
and the field lists are only there to override. Databases are the case you
normally configure, since only you know which columns are worth searching.

And some things deliberately aren't searched:

| Object | What happens |
|---|---|
| code and documents (PDF, Markdown, docx…) | converted to text and embedded — fully searchable |
| each table's schema | a small summary, so you can search the structure ("which table has an email column?") |
| an image | embedded only if image descriptions are turned on |
| raw structured text — `.json`, `.csv`, `.yaml`, `.log` | you can browse and `grep` it, but it isn't embedded for semantic search |
| a binary, or anything marked not-indexable | kept for browsing only |

## One command reopens any result

A search result is only useful if you can reopen exactly what it found — a few
lines of a file, or one row of a table. So every result comes back with two
things: which object it's in, and a small `locator` for the spot inside it. The
same `cat` reopens either one:

```bash
# result in a code file → the locator is a line range
mfs cat file://local/repo/engine.py --locator '{"lines":[42,78]}'

# result in a database table → the locator is the row's key
mfs cat postgres://prod/public/tickets/rows.jsonl --locator '{"id":12345}'
```

You never have to learn a different way to address each source. A result from
anywhere is something you reopen with the same command.

## Every source reports changes the same way

When you re-run `mfs add`, MFS redoes only what actually changed. Each kind of
source works out "what changed" in its own way — but they all report it back the
same way (added, changed, removed), so the update logic is written once:

| Source family | What changed is found by | For example |
|---|---|---|
| **Files & blobs** — file, S3, Drive, web, repo code | re-scanning and comparing content hashes | edit `README.md` and its hash changes, so it's re-indexed; delete it and the full re-scan notices it's gone |
| **Databases & warehouses** — Postgres, Mongo, BigQuery… | reading an `updated_at`-style column | 12 rows change in a million-row table, and only those 12 are re-pulled |
| **Messages & mail** — Slack, Gmail, Feishu… | continuing past the newest message it already has | 30 new messages since last time, so only those are fetched; old threads are left alone |
| **Issues, CRM & docs** — Jira, Linear, Notion… | checking an "updated" timestamp | reopen a Jira ticket and it's re-indexed on the next sync |

## What's built once, and what a new source adds

Everything above is what makes the last part true: **adding a new source is a
small job, because all the hard parts already exist.** Someone writing a connector
for a new tool doesn't build a search engine — they write a thin adapter that
answers a few questions about their source, and get everything else for free:

| Built once, in MFS — a new source just reuses it | What a new source has to provide |
|---|---|
| splitting, embedding, image descriptions, summaries | how to connect and sign in |
| the search index and ranking | the tree layout — what its folders and objects look like |
| `ls` / `cat` / `grep` / `search` and the HTTP API | which kind each object is |
| the job queue, caching, crash recovery, deletions | how to read an object, and how to tell what changed |

In practice that adapter is a handful of small functions — often a few hundred
lines — and the source is then searchable and browsable like everything else.
There are a couple of **optional** extras for sources that can do better, and MFS
falls back to its general path when they can't:

- a database can answer a `grep` with a SQL `WHERE` clause instead of scanning;
- a source with a real timestamp can support `mfs add --since 2026-01-01`.

That's the whole reason for pinning everything to one model: the common, expensive
part is written once, and the list of supported sources grows just by adding thin
adapters. It's the same trade that lets a shell drive thousands of programs
through a few file commands — make the shared part the same, keep the differences
small. See [Design philosophy](production.md),
[Architecture](architecture.md#core-concepts), and [Connectors](connectors.md).
