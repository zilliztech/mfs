---
name: mfs
version: 0.4.0
mfs_compat: ">=0.4,<0.5"
description: Search, browse, and read across large indexed multi-source collections via the `mfs` CLI — codebases, docs, PDFs, web crawls, databases (postgres/mysql/mongo/snowflake/bigquery), issue trackers (jira/linear/github), CRMs (salesforce/hubspot), chat (slack/discord/gmail/feishu), object stores (s3/gdrive). Use whenever the user asks to find, search, look up, or locate something across a large repo, database, workspace, issue tracker, or cross-source collection — even if they don't say "MFS". Trigger phrases include "search the codebase for", "find anywhere about", "where is X mentioned", "look across our [slack/jira/postgres/etc]", "any past tickets/RFCs/commits about", "what does our wiki say about". Do NOT use for: a known single file (use `cat`/`grep` directly), a single record fetched by exact id (use `mfs cat --locator` directly without searching first), or any write/delete operation — MFS is read-only.
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

**Borderline cases — when in doubt, ASK the user**:

| Ask | Likely answer | Why |
|---|---|---|
| "Summarise these 10 PDFs" | ✅ MFS — `mfs add <folder>`, then `mfs search` + `cat --peek` per top hit | each PDF gets a `converted_md` artifact + searchable chunks |
| "Find similar tickets to this one" | ✅ MFS — paste the ticket's content as the search query | semantic search over `row_text` chunks does similarity matching |
| "Watch for new slack messages" | ❌ MFS — index lags ingest; use Slack's own API for real-time | MFS has no `watch` capability today |
| "Look up user 12345's full profile" | ❌ MFS — use source's native API, or `mfs cat --locator '{"id":12345}'` if connector already registered (skip the search step entirely) | one-record-by-id doesn't need ranking |

When in doubt, a one-line clarification beats running the wrong command.

## 3. What kind of task is this? — task router

Three flavours of MFS task. Identify which one first; jumping into
commands without knowing is the most common skill mis-use.

| Flavour | Looks like | Read |
|---|---|---|
| **Query / Read** | "find X anywhere", "what do our docs say about Y", "where is Z mentioned" | §4 Workflow (search → locate → browse) |
| **Setup** | "index our jira / postgres", "add this repo to MFS", "make X searchable" | §5 Setup playbook + the matching `references/connectors/<scheme>.md` BEFORE writing TOML |
| **Maintain / Diagnose** | "search returns nothing", "this connector is stuck", "sync failed", "remove this source" | §6 Diagnosis playbook |

When the user's request is ambiguous (e.g. "look at our database" — query?
setup? maintenance?), **ASK** before running anything. A 2-line
clarification beats two wrong `mfs add`s.

## 4. The core workflow: **search → locate → browse**

```
        search                locate                  browse
  ┌──────────────────┐   ┌──────────────────┐   ┌─────────────────────┐
  │  semantic + BM25 │ → │ result has lines │ → │  cat --range / cat  │
  │  finds candidate │   │  or a locator    │   │  --peek to confirm  │
  │  files / rows    │   │  → reopen exact  │   │  context            │
  └──────────────────┘   └──────────────────┘   └─────────────────────┘
```

**On large collections** (5000-file repos, 50k-ticket queues, big
cross-source workspaces) this loop is the whole point: reading whole
files to scan them doesn't scale, but the index lets you ask in natural
language, get back ranked candidates with exact line/record handles, and
**read only the part that matters** to verify. Minutes vs hours.

**On small collections** (a few dozen files, one project's docs) the
same toolset still works, lighter: either `mfs search` and skip the
locate step (the snippet is often enough), or skip search entirely and
flip through with `mfs ls / tree / cat --peek` — the index isn't doing
much extra work on this size, so optimise for whichever feels more
direct.

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

## 5. Setup playbook — registering a new source

Adding a source costs API tokens (embedding + optional VLM/summary) and
wall-clock time. The flow below is the SAME for every connector; the
connector-specific details (URI shape, auth, TOML keys, gotchas) live in
`references/connectors/<scheme>.md` and **MUST** be read before step 2.

### Pre-flight

```bash
mfs --version              # missing? `uv tool install mfs`
mfs status                 # server reachable? if not, `mfs serve start`
                           # (needs `uv tool install mfs-server`) on a
                           # personal machine; remote profiles already hosted
```

This skill targets `mfs_compat: >=0.4,<0.5`. If versions diverge, update
the lagging side: `uv tool upgrade mfs` / `uv tool upgrade mfs-server`.

### The five steps

1. **Probe credentials manually** before MFS sees them. 30 seconds of
   `curl -u $email:$token <api>` (SaaS) or `psql "$DSN" -c 'SELECT 1'` (DB)
   beats a 30-minute `mfs add` failing on auth. **STOP and ASK the user**
   if the source has multiple credential types (e.g. HubSpot's Service App
   vs Private App, Jira's three token kinds, Slack's bot vs user token)
   and you can't tell which they meant.

2. **Write the TOML config**. Required fields per connector are in the
   matching `references/connectors/<scheme>.md`. **ASK the user** rather
   than guess when:
   - the field is domain-specific (`text_fields` for a postgres table — ask
     which column carries the searchable content; don't invent the list)
   - the connector has tier-gated features (Service Hub on HubSpot,
     `tickets` on a Free CRM, JSONB paths on postgres) and the user's tier
     isn't clear from earlier context
   - the user has multiple sources of the same type and didn't specify
     which (which workspace? which database? which project?)

3. **Estimate cost** before committing:
   ```bash
   mfs add <uri> --config <toml> --estimate
   ```
   Enumerates the source (no embedding), reports approximate chunks /
   tokens / time. **ASK the user before continuing** when the estimate
   crosses any of:
   - non-trivial embedding spend (tens of dollars+ at the configured
     model's rate),
   - tens of thousands of records,
   - hours of indexing wall-clock time.

   For large sources, also ask whether to cap via `max_read_rows` /
   `max_file_bytes` / per-object filters before the first full sync.

4. **Run `mfs add`** (returns a job id; async by default):
   ```bash
   mfs add <uri> --config <toml>           # async
   mfs add <uri> --config <toml> --wait    # block — only for small sources
   mfs add <uri> -y                        # skip estimate/confirm prompt
   ```

5. **Verify before declaring done**:
   ```bash
   mfs status <uri>                                 # available / partial / building
   mfs ls <uri> --json                              # per-object search_status
   mfs search "<one known term>" --connector-uri <uri> --top-k 3   # smoke check
   ```
   If the smoke check returns nothing or wrong content, jump to §6.

### Index-requirement rules of thumb

- `mfs search` **requires** an index — `mfs add` first if absent.
- `mfs grep` works WITHOUT an index (connector pushdown → BM25 if
  indexed → linear scan fallback).
- `mfs ls / tree / cat / head / tail` browse WITHOUT an index.

## 6. Diagnosis playbook — when results look wrong or stuck

Skipping the diagnosis ladder is the second-most-common skill mis-use
after mis-identifying task flavour. Don't rewrite the query before
checking these rungs, in order. Stop on the first hit.

```bash
# 1. is the server reachable? are any connectors registered at all?
mfs status

# 2. THIS connector's search availability
mfs status <uri>           # available | partial | building | unavailable

# 3. failed sync jobs for this connector?
mfs job ls
mfs job logs <job-id>      # if any in 'failed', read the cause

# 4. per-object granularity — partials mixed in?
mfs ls <uri> --json        # each entry's search_status field

# 5. structured error code path
# If a recent --json output carried a `code` field, open
references/error-codes.md  # for the specific recovery action

# 6. otherwise, treat it as a query-level problem (see §10)
```

### How to read the signals

| Signal | Meaning | Action |
|---|---|---|
| `building` | sync in flight | wait, or fall back to `mfs grep` until done |
| `partial` | some chunks dropped (`chunk_max` truncation, `max_read_rows` cap) | recall incomplete but usable; ASK user before raising caps (more cost) |
| `unavailable` | nothing indexed | only `grep` / `ls` / `cat` work; check `mfs job ls` for the failed sync |
| `job failed` | connector error | message + `references/error-codes.md` carry the recovery |
| smoke search returns nothing on a freshly `available` connector | wrong `text_fields`, wrong `object_types`, source is empty | re-check the TOML against `references/connectors/<scheme>.md`; `mfs cat` a known object to confirm content is there |

**ASK the user during diagnosis** when:
- a sync has been `building` longer than expected — cancel and retry, or
  keep waiting? Depends on the user's deadline.
- a `partial` recovery means "raise `chunk_max`" or "raise `max_read_rows`"
  — more chunks = more cost. Confirm before applying.
- multiple connectors carry related content and answer the query
  differently — which is canonical for this question?

## 7. Semantic search modes

`mfs search` defaults to `hybrid`, which is the right answer most of the
time. Override only when you know why.

| `--mode` | What it does | When to pick it |
|---|---|---|
| **`hybrid`** *(default)* | dense (meaning) + BM25 (keywords), fused with RRF | almost always — best general recall |
| `semantic` | dense vectors only | conceptual query where wording won't match the source's wording ("rate limiting strategy", "graceful failover") |
| `keyword` | BM25 sparse only | when you want exact-term scoring without semantic drift (a config key, a CLI flag name) |

Other useful search flags:

- `--top-k N` — number of hits (default 10; raise to 20-30 to compare
  more candidates on a weak first round).
- `--all` — search every registered connector at once. Otherwise scope to
  a path/URI prefix.
- `--kind <list>` — restrict to chunk kinds, e.g.
  `--kind row_text,schema_summary` to skip directory summaries.
- `--collapse` — fold multiple hits from the same object into one row.

### When to use `--all` (and when NOT to)

- ✅ **Cross-source recall** — "any past tickets / commits / RFCs / slack
  about X" type questions, where the asker genuinely doesn't know which
  source holds the answer.
- ❌ **You already know the source** — "any slack messages about X" should
  scope to `slack://`; postgres + jira + docs results aren't comparable.
- ⚠ **More than ~5 registered connectors** — results from very different
  source types (DB rows vs slack threads vs PDFs) become hard to rank
  side-by-side. **ASK the user** whether to fan out widely or scope to
  the 2-3 likeliest sources first.

## 8. Decision tree — pick the smallest useful tool

For each sub-task, identify the signal in the user's ask, then pick:

| Signal in the ask | The sub-task is… | Use |
|---|---|---|
| natural-language question / sentence | exploratory intent | `mfs search "<q>" <scope>` (default `hybrid`) |
| paraphrased / conceptual wording, query won't appear literally | semantic-only intent | `mfs search --mode semantic` |
| exact identifier / error code / config key / unique phrase in quotes | literal anchor | `mfs grep "<literal>" <path>` (or plain `grep`/`rg`) |
| filename / directory pattern | path lookup | `find` / shell glob / `fd` |
| known file + needs outline / section map | structural overview | `mfs cat --peek <file>` |
| known file + needs compact overview with snippets | dense overview | `mfs cat --skim <file>` |
| search hit + needs surrounding context | reopen | `mfs cat <file> --range <start>:<end>` |
| structured hit (row / issue / thread / record) | reopen by PK | `mfs cat <source> --locator '{...}'` |
| several close candidates | compare | `mfs cat --peek` each, then pick |
| single record + known key | no-search lookup | `mfs cat <source> --locator '{"id":12}'` |
| first / last N lines of an object | sampling | `mfs head -n N` / `mfs tail -n N` |
| subtree shape | orientation | `mfs tree -L 2 <uri>` |
| full object for offline tooling (jq / awk / grep) | export | `mfs export <uri> <file>` |
| connector / job / search-availability state | observability | `mfs status [<uri>]`, `mfs job ls`, `mfs connector ls` |

`mfs search` requires an explicit scope (`<path>`) or `--all`.

## 9. Command cheat sheet

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

### `mfs add` — register + index (see §5 for the full playbook)

```bash
mfs add <path-or-uri> [--config <toml>]   # async, returns job id; --estimate first
mfs add <uri>                             # re-run for incremental diff
# Flags: --wait | --full | --no-upload / --upload | -y (skip estimate prompt)
```

### `mfs export`

```bash
mfs export <uri> <out-file>     # full object to disk; use for jq/awk pipelines
```

`cat` of a huge lazy object is refused — use `export` for bulk processing.

### Status / management

```bash
mfs status                      # server + all connectors
mfs status <uri>                # one connector's per-object search_status
mfs connector ls                # list connectors
mfs job ls                      # in-flight indexing jobs
mfs remove <uri>                # drop a connector + its index data
```

Always prefer `--json` (where supported) when output will be parsed.

## 10. Weak search results → recover, don't thrash

If the top hits look off-topic:

1. **Rewrite the query** with synonyms / more domain context. **ASK the
   user** for the domain term they'd actually use if the ask is vague
   ("look for the rate-limit thing" — is it "throttle", "quota",
   "rate_limit", or a specific config key?). One clarifier beats five
   blind queries.
2. **Raise `--top-k`** to compare distinct candidates.
3. **`mfs cat --peek`** the top few to compare structure.
4. **Switch to `--mode semantic`** if the original was hybrid and the
   keywords are noisy; or `--mode keyword` if specific terms should be
   the anchor.
5. **Then** consider literal `grep` — but only if the task has a real
   literal anchor (error code, config key, identifier).

Don't grep the same vague words you searched. Literal search is a
*different* tool, not a stronger version of semantic search.

## 11. Candidate selection

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

## 12. Common anti-patterns

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
- **Don't `mfs add` a non-trivial source without estimating cost first.**
  `--estimate` is cheap; an unexpected six-figure embedding bill is not.
- **Don't silently guess domain config** (`text_fields`, `object_types`,
  `projects` filters, JSONB paths). When in doubt, ASK the user — these
  are bake-in choices that re-syncs don't easily undo.
- **Don't `--full` re-index** to "fix" something you haven't diagnosed —
  full re-embed wastes tokens. Walk §6 first.

## 13. Route to the right reference

Most common guidance is already in this file. The references below are
loaded ONLY when the situation matches — don't open them speculatively.

- **[`references/json-envelope.md`](references/json-envelope.md)** —
  Open WHEN parsing a `--json` search/grep result and the `locator`
  shape is unfamiliar (composite PKs, `thread_ts`, nested keys); OR
  when uncertain how to feed a hit back into `mfs cat` (range vs locator
  dispatch). Skip if §4 above already gave you a working reopen
  command.

- **[`references/error-codes.md`](references/error-codes.md)** —
  Open WHEN an `mfs` command returned `--json` error output with a `code`
  field and you need the specific recovery action; OR when a sync / job
  stalled with an unfamiliar error category. Don't open just because a
  command failed — read the error message first.

- **`references/connectors/<scheme>.md`** —
  Open WHEN: (a) about to run `mfs add <new-uri>` for a connector scheme
  you haven't used in this session — the file documents URI shape, auth
  setup, TOML config keys, and per-command behaviour for that connector;
  (b) an existing connector's command is behaving unexpectedly (auth
  fail, missing fields, weird locator shape, partial state) and the
  symptom matches no `mfs` error code. STOP and read the matching one
  BEFORE guessing the URI layout, TOML keys, or how the connector
  enumerates objects. Available schemes: `file`, `web`, `s3`, `gdrive`,
  `postgres`, `mysql`, `snowflake`, `bigquery`, `mongo`, `github`,
  `jira`, `linear`, `hubspot`, `salesforce`, `notion`, `zendesk`,
  `slack`, `discord`, `gmail`, `feishu`.

Runtime capability for a specific URI is queried structurally via
`mfs ls <uri> --json` (`capabilities`, `search_status`); the static
per-connector references describe what the connector exposes by design.
