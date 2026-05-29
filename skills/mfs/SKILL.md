---
name: mfs
version: 0.4.0
mfs_compat: ">=0.4,<0.5"
description: Use this skill to search, browse, and read across large, indexed multi-source collections — codebases, docs, PDFs, images, web crawls, GitHub repos, databases (Postgres/MySQL/Mongo/Snowflake/BigQuery), CRMs (Salesforce/HubSpot), issue trackers (Jira/Linear), chat (Slack/Discord/Gmail/Feishu), object stores (S3/R2/GCS) — through the MFS shell-native CLI. MFS earns its keep on LARGE collections by building a hybrid (semantic + keyword) index so search is fast and high-recall, then you locate the exact spot and browse nearby. For tiny scopes or known literals, plain grep/read is simpler.
---

# MFS — Multi-source File-like Search

## 1. Background

MFS is a shell-native retrieval layer that makes many kinds of content
behave like a filesystem and makes their content searchable through a
single index.

- **One CLI (`mfs`), one mental model.** Every source — local directory,
  Postgres database, GitHub repo, Slack workspace, S3 bucket, BigQuery
  dataset — is exposed as a path tree, and the same verbs work everywhere:
  `ls / tree / cat / head / tail / grep / search / export`.
- **One hybrid index, many sources.** All indexable content lands in the
  same Milvus collection: dense vectors for semantic similarity + BM25
  sparse vectors for exact-keyword recall, fused at query time.
- **POSIX-style locator semantics.** A search result tells you exactly
  where the hit is — line range for text/code, primary-key locator for
  rows/issues/threads — and the same handle reopens that precise unit.

20 connectors ship in this version, split by content shape:

| Shape | Connectors |
|---|---|
| File-like (text/code/docs/images) | `file`, `web`, `s3`, `gdrive` |
| Relational tables | `postgres`, `mysql`, `snowflake`, `bigquery` |
| Document store | `mongo` |
| Record collections (issues / objects) | `github`, `jira`, `linear`, `hubspot`, `salesforce`, `notion`, `zendesk` |
| Message streams (threads aggregated) | `slack`, `discord`, `gmail`, `feishu` |

Each has its own reference under
`references/connectors/<scheme>.md` — read the one matching the URI scheme
before guessing how that source is laid out.

## 2. Where MFS shines (and where it doesn't)

| Situation | Use MFS? |
|---|---|
| 1000+ files / rows / pages, you don't know where the answer is | ✅ this is what the index is for |
| Cross-source question ("any past tickets / commits / RFCs about X") | ✅ one query against `--all` |
| Concept/intent-style query that won't match literally | ✅ semantic mode |
| You already know the exact file + roughly where to look | ❌ plain `cat`/`grep` is faster |
| Exact identifier / error code in 5 files you can list | ❌ plain `grep`/`rg` is fine |
| Mutations (write/delete/UPSERT) on the source | ❌ MFS is read-only |
| Real-time tailing of a live log | ❌ index lags behind ingest |

**Rule:** use the smallest tool that answers the question. MFS is a
heavyweight stack — fire it up when the scope is too big for `rg`.

## 3. The core workflow on large collections: **search → locate → browse**

```
        search                locate                  browse
  ┌──────────────────┐   ┌──────────────────┐   ┌─────────────────────┐
  │  semantic + BM25 │ → │ result has lines │ → │  cat --range / cat  │
  │  finds candidate │   │  or a locator    │   │  --peek to confirm  │
  │  files / rows    │   │  → reopen exact  │   │  context            │
  └──────────────────┘   └──────────────────┘   └─────────────────────┘
```

**Why this loop wins on large data.** Reading whole files just to scan
them doesn't scale. The index lets you ask in natural language, get back
ranked candidates with exact line/record handles, and **read only the
part that matters** to verify. For a 5000-file repo or a 50k-ticket
queue, this is the difference between minutes and hours.

Each step in concrete form:

1. **Search** — point semantic + keyword recall at a scope:
   ```bash
   mfs search "<what the user actually wants>" <path-or-uri> --top-k 10
   ```

2. **Locate** — every hit's envelope carries `locator` (one unified field):
   - Text/code hit → `locator: {"lines":[start,end]}` →
     `mfs cat <source> --range start:end`
   - Structured hit (DB row, issue, slack thread) → `locator: {<pk>}` →
     `mfs cat <source> --locator '{...}'`
   - Once-per-object kinds (dir/schema summary, image VLM) → `locator: null` →
     `mfs cat <source>`

3. **Browse** — verify, only as much as needed:
   ```bash
   mfs cat --peek <file>      # heading/symbol skeleton
   mfs cat --skim <file>      # + one-line summaries per section
   mfs head -n 20 <uri>       # first records of a structured object
   mfs tree <uri> -L 2        # subtree shape
   ```

Detailed playbook (weak-result recovery, multi-part prompts, candidate
comparison): **[references/workflow.md](references/workflow.md)**.

## 4. Semantic search modes

`mfs search` defaults to `hybrid`, which is the right answer most of the
time. Override only when you know why.

| `--mode` | What it does | When to pick it |
|---|---|---|
| **`hybrid`** *(default)* | dense (meaning) + BM25 (keywords), fused with RRF | almost always — best general recall |
| `semantic` | dense vectors only | conceptual query where wording won't match the source's wording ("rate limiting strategy", "graceful failover") |
| `keyword` | BM25 sparse only | when you want exact-term scoring without semantic drift (a config key, a CLI flag name) |

Other useful search flags:

- `--top-k N` — number of hits (default 10; raise to 20-30 to compare more candidates on a weak first round).
- `--all` — search every registered connector at once. Otherwise scope to a path/URI prefix.
- `--kind <list>` — restrict to chunk kinds, e.g. `--kind row_text,schema_summary` to skip directory summaries.
- `--collapse` — fold multiple hits from the same object into one row.

## 5. Environment prep (do this before relying on MFS)

**a) CLI installed and version-aligned.**

```bash
mfs --version
```

- Missing? Install: `uv tool install mfs` (or `brew install zilliztech/tap/mfs`).
- This skill targets `mfs_compat: >=0.4,<0.5`. If versions diverge, update
  whichever side lags only if the task needs it: `uv tool upgrade mfs` /
  `uv tool upgrade mfs-server`.

**b) Server reachable.**

```bash
mfs status     # server up? connectors? index freshness? jobs running?
```

If no local server is running and this is a personal machine:
`mfs serve start` (needs `uv tool install mfs-server`). On a remote/team
profile the server is already hosted — just point your profile at it.

**c) Target is indexed (only needed for `search`).**

- `mfs search` **requires** an index → `mfs add <path-or-uri>` first if absent.
- `mfs grep` works WITHOUT an index (connector pushdown → BM25 if indexed → linear scan fallback).
- `mfs ls / tree / cat / head / tail` browse WITHOUT an index.
- `mfs status <uri>` shows per-connector availability
  (`available / partial / building / unavailable`) and per-object `search_status`.
- For a large source, **ask before starting a big `mfs add`** unless the user already requested it. `mfs add` is async; poll `mfs status <uri>` until search is `available`.

## 6. Decision tree — pick the smallest useful tool

For each sub-task, pick:

| The sub-task is… | Use |
|---|---|
| Natural-language intent ("how does X work", "anything about Y", "where do we…") | `mfs search "<q>" <scope>` (default `hybrid`) |
| Conceptual / paraphrased intent, query wording differs from source | `mfs search --mode semantic` |
| Exact identifier, error code, config key, unique phrase, URL | `mfs grep "<literal>" <path>` (or plain `grep`/`rg`) |
| Filename / directory pattern | `find` / shell glob / `fd` |
| Known file, need outline / section map | `mfs cat --peek <file>` |
| Known file, need compact overview with snippets | `mfs cat --skim <file>` |
| Search hit relevant but you need surrounding context | `mfs cat <file> --range <start>:<end>` around the hit |
| Structured hit (row / issue / thread / record) | `mfs cat <source> --locator '{...}'` |
| Several close candidates, need to compare structure | `mfs cat --peek` each, then pick |
| Single record by key, no search involved | `mfs cat <source> --locator '{"id":12}'` (works without search) |
| First / last N lines of an object | `mfs head -n N` / `mfs tail -n N` |
| Subtree shape, recently changed sources | `mfs tree -L 2 <uri>` |
| Full object for offline tooling (jq / awk / grep) | `mfs export <uri> <file>` |
| Connector / job / search-availability state | `mfs status [<uri>]`, `mfs job ls`, `mfs connector ls` |

`mfs search` requires an explicit scope (`<path>`) or `--all`.

## 7. Command cheat sheet

### `mfs search` — semantic + keyword

```bash
mfs search "<query>" <path-or-uri>             # default: hybrid, top-k=10
mfs search "<query>" --all                     # whole namespace
mfs search "<query>" <path> --top-k 20         # more candidates
mfs search "<query>" <path> --mode semantic    # dense-only
mfs search "<query>" <path> --mode keyword     # BM25-only
mfs search "<query>" <path> --kind row_text    # restrict chunk kinds
mfs search "<query>" <path> --collapse         # dedup hits per object
```

### `mfs grep` — literal/keyword

```bash
mfs grep "<pattern>" <path>          # connector pushdown -> BM25 -> linear
```

Use `grep`/`rg` directly when you're already on a known local subtree.
`mfs grep`'s precision varies by source — pushdown (SQL `LIKE`, BM25) is
literal-exact but token-level, not regex. For exact-exhaustive, use a
pushdown source or `mfs export` then local `grep`.

### `mfs cat` — read object / range / single record

```bash
mfs cat <path>                                  # full content (refused if "lazy")
mfs cat <path> --range A:B                      # byte/line range slice
mfs cat <path> --locator '{"id":12}'            # reopen a structured record
mfs cat <path> --peek                           # skeleton (headings/symbols)
mfs cat <path> --skim                           # peek + one-line section summaries
mfs cat <path> --meta                           # stat-style metadata, not content
```

**Density ladder**:

| Mode | Use it when |
|---|---|
| `--peek` | "show me the outline" — headings / function signatures only |
| `--skim` | peek + a one-line summary per section, still concise |
| (default) | full content, only if the file is small or you really need it |
| `--range A:B` | you already know which lines matter (e.g. a search hit) |

### `mfs head` / `mfs tail`

```bash
mfs head -n 50 <path>           # first 50 lines/records
mfs tail -n 50 <path>           # last 50; Rust-accelerated reverse read
```

For a lazy `rows.jsonl` (DB / SaaS record collections), `head` is the
right way to see what shape the records have without paying the full-scan
cost.

### `mfs tree` / `mfs ls`

```bash
mfs ls <uri>                    # one level
mfs tree <uri> -L 2             # depth-bounded recursive
```

Use to orient on a new source's layout. NOT a substitute for `search`
when the target is unknown and conceptual.

### `mfs add` — register + index

```bash
mfs add <path-or-uri>                                # default: async, returns job id
mfs add <path-or-uri> --config <file.toml>           # for non-file connectors
mfs add <path-or-uri> --wait                         # block until indexing finishes
mfs add <path-or-uri> --full                         # force a full re-index
mfs add <path-or-uri> --no-upload                    # local file, shared filesystem (server reads directly)
mfs add <path-or-uri> --upload                       # bundle + send to server (no shared fs)
mfs add <path-or-uri> -y                             # skip the estimate/confirm prompt
```

For incremental refresh after upstream changes:
```bash
mfs add <uri>                   # by default, syncs only the diff
```

### `mfs export`

```bash
mfs export <uri> <out-file>     # full object to disk; use for jq/awk pipelines
```

The right way to bulk-process a structured object — `cat` of a huge lazy
object is refused.

### Status / management

```bash
mfs status                      # server + all connectors
mfs status <uri>                # one connector's per-object search_status
mfs connector ls                # list connectors
mfs job ls                      # in-flight indexing jobs
mfs remove <uri>                # drop a connector + its index data
```

Always prefer `--json` (where supported) when output will be parsed.

## 8. Weak search results → recover, don't thrash

If the top hits look off-topic:

1. **Rewrite the query** with synonyms / more domain context.
2. **Raise `--top-k`** to compare distinct candidates.
3. **`mfs cat --peek`** the top few to compare structure.
4. **Switch to `--mode semantic`** if the original was hybrid and the
   keywords are noisy; or `--mode keyword` if specific terms should be
   the anchor.
5. **Then** consider literal `grep` — but only if the task has a real
   literal anchor (error code, config key, identifier).

Don't grep the same vague words you searched. Literal search is a
*different* tool, not a stronger version of semantic search.

## 9. Candidate selection

Think at the object level, not just the chunk level:

- **Merge** repeated hits from the same object into one candidate mentally.
- **Compare** the top distinct candidates' `--peek` when titles or
  snippets look adjacent.
- **Prefer** an object whose main topic directly matches the request over
  a broad overview that contains one relevant paragraph.
- **For multi-part prompts** (the user mentions two entities / a setup +
  troubleshooting / a migration's source + target), check whether more
  than one object is needed.

Useful comparison pattern:
```bash
mfs search "<query>" <path> --top-k 20
mfs cat --peek <candidate-a>
mfs cat --peek <candidate-b>
mfs cat <best> --range <start>:<end>
```

## 10. Common anti-patterns

- **Don't grep to "confirm" a successful semantic hit.** The hit's
  snippet IS the source content; trust it and read the range.
- **Don't read a whole large file** when `--peek` / `--skim` / `--range`
  can answer.
- **Don't blindly pick rank #1** when ranks #1-#3 are clearly different
  objects covering related topics.
- **Don't stop at one match** if the prompt mentions multiple entities
  or actions.
- **Don't search the same vague words after a weak first round** — fix
  the query or escalate to literal anchors.
- **Don't `cat` a lazy object** (DB `rows.jsonl`, SaaS `records.jsonl`).
  Use `head`, `--range`, `--locator`, or `export`.
- **Don't use MFS for sources you'd just clone/download anyway** — pull
  them locally and use the `file` connector.

## 11. Route to the right reference

Most common guidance is already in this file. Open these only when you
need more detail:

- **Workflow patterns + scoping + recovery** → [references/workflow.md](references/workflow.md)
- **Result envelope fields (source / locator / content / metadata)** → [references/json-envelope.md](references/json-envelope.md)
- **Error codes and recovery** → [references/error-codes.md](references/error-codes.md)
- **Per-connector reference** (URI shape, auth, TOML config, command behaviour, gotchas) → `references/connectors/<scheme>.md`. Read the one matching the URI scheme **before** registering a new source or guessing its layout. Available schemes: `file`, `web`, `s3`, `gdrive`, `postgres`, `mysql`, `snowflake`, `bigquery`, `mongo`, `github`, `jira`, `linear`, `hubspot`, `salesforce`, `notion`, `zendesk`, `slack`, `discord`, `gmail`, `feishu`.

Runtime capability for a specific URI is queried structurally via
`mfs ls <uri> --json` (`capabilities`, `search_status`); the static
per-connector references describe what the connector exposes by design.
