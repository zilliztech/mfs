---
name: mfs-find
version: 0.4.0
mfs_compat: ">=0.4,<0.5"
description: Search, grep, browse, and read across registered MFS data sources via the `mfs` CLI — codebases, docs, PDFs, web crawls, databases (postgres/mysql/mongo/snowflake/bigquery), issue trackers (jira/linear/github), CRMs (salesforce/hubspot), chat (slack/discord/gmail/feishu), object stores (s3/gdrive). Use whenever the user asks to find, locate, look up, look across, or read something out of an already-configured MFS index. Trigger phrases include "search the codebase for", "find anywhere about", "where is X mentioned", "look across our [slack/jira/postgres/etc]", "any past tickets/RFCs/commits about", "what does our wiki say about", "cat / head / tail / ls / tree this MFS path". Do NOT use for: registering a NEW data source (use `mfs-ingest`), changing connector config, kicking off re-ingest, or any write/delete operation — `mfs` is read-only.
---

# MFS — find / read across configured sources

## 1. What MFS is

A retrieval layer that exposes many kinds of content as a unified path
tree and makes that tree searchable through one hybrid index:

- **One CLI (`mfs`), one mental model.** Local dir, Postgres, GitHub repo,
  Slack workspace, S3 bucket, BigQuery dataset — all addressed as paths
  under their `<scheme>://` URI. Same verbs everywhere: `ls / tree / cat /
  head / tail / grep / search / export`.
- **One hybrid index.** Dense vectors (semantic) + BM25 (keyword) fused
  per query — covers conceptual recall and exact-token recall in one call.
- **POSIX-style locators.** Every search hit carries a `locator` that
  reopens the exact unit: `{"lines":[s,e]}` for text/code, a PK dict for
  rows/issues/threads.

## 2. When to use MFS — and when NOT to

| Situation | Use MFS? |
|---|---|
| 1000+ files / rows / pages, you don't know where the answer is | ✅ |
| Cross-source question ("any past tickets / commits / RFCs about X") | ✅ `--all` |
| Concept-style query that won't match literally | ✅ `--mode semantic` |
| You already know the exact file + roughly where to look | ❌ plain `cat`/`grep` |
| Exact identifier / error code in 5 files you can list | ❌ plain `grep`/`rg` |
| Real-time tailing of a live log | ❌ index lags ingest |
| The source isn't in MFS yet | wrong skill, use `mfs-ingest` to register first |

**Rule:** use the smallest tool that answers the question. MFS pays off
when the scope is too big for `rg`.

**Borderline — ASK the user:**

| Ask | Likely answer | Why |
|---|---|---|
| "Summarise these 10 PDFs" | ✅ `mfs search` + `cat --peek` per hit | each PDF gets a `converted_md` artifact + searchable chunks |
| "Find similar tickets to this one" | ✅ paste the ticket text as the search query | semantic over `row_text` does similarity matching |
| "Watch for new slack messages" | ❌ no `watch` capability; use Slack's API | index lags ingest |
| "Look up user 12345" | ❌ `mfs cat <source> --locator '{"id":12345}'` directly (skip search) | one-record-by-id doesn't need ranking |

## 3. Pre-flight — confirm the source is indexed

Before running any query, especially on cross-source asks:

```bash
mfs status                  # server up? any connectors registered?
mfs status <uri>            # this source's per-object search_status
mfs ls <uri> --json         # see capabilities + indexable / search_status
```

- Server unreachable → tell user to start it (`mfs serve start` if
  self-hosted), or this skill can't proceed.
- `connectors` empty → user hasn't ingested anything yet. **Redirect to
  `mfs-ingest`** — don't try to search nothing.
- `search_status: unavailable` for the target URI → only `grep` / `ls` /
  `cat` work; offer those or redirect to `mfs-ingest` for a re-sync.
- `building` → sync in flight; fall back to `mfs grep` (works without an
  index) until done.
- `partial` → recall incomplete but usable; flag the caveat to the user.

## 4. The core workflow: search → locate → browse

```
        search                 locate                browse
  ┌──────────────────┐   ┌──────────────────┐   ┌─────────────────────┐
  │  semantic + BM25 │ → │ result has lines │ → │  cat --range / cat  │
  │  finds candidates│   │  or a locator    │   │  --peek to confirm  │
  └──────────────────┘   └──────────────────┘   └─────────────────────┘
```

On large corpora this loop is the whole point: read only the part that
matters. On small corpora it's still fine, just lighter.

Concrete:

1. **Search:**
   ```bash
   mfs search "<what the user actually wants>" <path-or-uri> --top-k 10
   ```
2. **Locate** — every hit's envelope carries `locator`:
   - text/code → `{"lines":[start,end]}` → `mfs cat <source> --range start:end`
   - structured (row/issue/thread) → PK dict → `mfs cat <source> --locator '{...}'`
   - once-per-object (dir/schema summary, image VLM) → `null` → `mfs cat <source>`
3. **Browse** — verify only what's needed:
   ```bash
   mfs cat --peek <file>       # outline (headings / function signatures)
   mfs cat --skim <file>       # peek + one-line summaries per section
   mfs head -n 20 <uri>        # first records of a structured object
   mfs tree <uri> -L 2         # subtree shape
   ```

## 5. Index requirement rules of thumb

- `mfs search` **requires** an index.
- `mfs grep` works **without** — pushdown → BM25 → linear scan fallback.
- `mfs ls / tree / cat / head / tail` browse **without** an index.

## 6. Search modes

`mfs search` defaults to `hybrid`. Override only when you know why.

| `--mode` | Mechanic | When |
|---|---|---|
| **`hybrid`** *(default)* | dense + BM25 fused with RRF | almost always |
| `semantic` | dense only | conceptual query, wording won't match literally |
| `keyword` | BM25 only | exact-term (config key, error code) without semantic drift |

Other useful flags:

- `--top-k N` — default 10; raise to 20-30 on a weak first round.
- `--all` — search every registered connector. Otherwise scope to a path/URI prefix.
- `--kind <list>` — restrict chunk kinds (`row_text`, `thread_aggregate`,
  `chunk_body`, `summary`, `vlm_description`, …).
- `--collapse` — fold multiple hits from the same object into one row.

### `--all`: when yes, when no

- ✅ Cross-source recall — "any past tickets / commits / RFCs / slack about X".
- ❌ You know the source — scope to `slack://`; postgres + jira + docs together aren't comparable.
- ⚠ More than ~5 registered connectors — ASK the user whether to fan out
  widely or scope to the 2-3 likeliest sources first.

## 7. Decision tree — pick the smallest useful tool

| Signal in the ask | Sub-task | Use |
|---|---|---|
| natural-language question / sentence | exploratory | `mfs search "<q>" <scope>` |
| paraphrased / conceptual wording | semantic-only | `mfs search --mode semantic` |
| exact identifier / config key / unique phrase | literal anchor | `mfs grep "<lit>" <path>` (or `rg`) |
| filename / directory pattern | path lookup | `find` / shell glob / `fd` |
| known file + needs outline | structural overview | `mfs cat --peek <file>` |
| known file + compact summary | dense overview | `mfs cat --skim <file>` |
| search hit + surrounding context | reopen | `mfs cat <file> --range s:e` |
| structured hit (row/issue/thread) | reopen by PK | `mfs cat <source> --locator '{...}'` |
| several close candidates | compare | `mfs cat --peek` each, then pick |
| single record + known key | no-search lookup | `mfs cat <source> --locator '{"id":12}'` |
| first / last N | sample | `mfs head -n N` / `mfs tail -n N` |
| subtree shape | orient | `mfs tree -L 2 <uri>` |
| full object for offline tooling | export | `mfs export <uri> <file>` |

`mfs search` requires an explicit scope or `--all`.

## 8. Command cheat sheet

### Search

```bash
mfs search "<query>" <path-or-uri>             # default: hybrid, top-k=10
mfs search "<query>" --all                     # whole namespace
mfs search "<query>" <path> --top-k 20         # more candidates
mfs search "<query>" <path> --mode semantic    # dense-only
mfs search "<query>" <path> --mode keyword     # BM25-only
mfs search "<query>" <path> --kind row_text    # restrict chunk kinds
mfs search "<query>" <path> --collapse         # dedup per object
```

### Grep

```bash
mfs grep "<pattern>" <path>          # pushdown -> BM25 -> linear
```

Pushdown is literal-exact but token-level (no regex on structured
connectors). For exact-exhaustive on a huge structured object, `mfs
export` then local `grep`.

### Read

```bash
mfs cat <path>                                  # full content (refused if "lazy")
mfs cat <path> --range A:B                      # byte/line range
mfs cat <path> --locator '{"id":12}'            # reopen a structured record
mfs cat <path> --peek                           # outline only
mfs cat <path> --skim                           # peek + per-section summaries
mfs cat <path> --meta                           # stat-style, not content
```

Density ladder:

| Mode | Use it when |
|---|---|
| `--peek` | "show me the outline" |
| `--skim` | + one-line summary per section, still concise |
| (default) | full content; small file or really need it |
| `--range A:B` | already know which lines matter (e.g. search hit) |

```bash
mfs head -n 50 <path>           # first 50 lines/records
mfs tail -n 50 <path>           # last 50; native-accel reverse read
```

For a lazy `rows.jsonl` / `messages.jsonl`, `head` is how to see record
shape without paying full-scan cost.

### Browse

```bash
mfs ls <uri>                    # one level
mfs tree <uri> -L 2             # depth-bounded recursive
```

NOT a substitute for `search` when the target is unknown and conceptual.

### Export

```bash
mfs export <uri> <out-file>     # full object to disk for jq/awk pipelines
```

`cat` of a huge lazy object is refused — use `export` for bulk processing.

### Status (useful before AND during search work)

```bash
mfs status                      # server + all connectors
mfs status <uri>                # one connector + per-object search_status
mfs connector ls                # list registered connectors
mfs job ls                      # in-flight indexing jobs (background re-syncs)
```

Always prefer `--json` when output will be parsed.

## 9. Weak results → recover, don't thrash

If top hits look off-topic:

1. **Rewrite** with synonyms / domain terms. ASK the user for the domain
   term they'd actually use if vague. One clarifier beats five blind queries.
2. **Raise `--top-k`** to compare distinct candidates.
3. **`mfs cat --peek`** the top few to compare structure.
4. **Switch mode** — semantic if hybrid was keyword-noisy; keyword if
   specific terms should be the anchor.
5. **Then** literal `grep` — only if the task has a real literal anchor
   (error code, config key, identifier).

Literal search is a *different* tool, not a stronger version of semantic.

## 10. Candidate selection

Think at object level, not just chunk level:

- **Merge** repeated hits from the same object into one candidate.
- **Compare** the top distinct candidates' `--peek` when titles or
  snippets look adjacent.
- **Prefer** an object whose main topic directly matches the request
  over a broad overview that mentions it.
- **Multi-part prompts** (two entities, setup + troubleshooting, migration
  source + target) — check whether more than one object is needed.

```bash
mfs search "<query>" <path> --top-k 20
mfs cat --peek <candidate-a>
mfs cat --peek <candidate-b>
mfs cat <best> --range <start>:<end>
```

## 11. Anti-patterns

- **Don't grep to "confirm" a successful semantic hit.** The hit's
  snippet IS the source content; trust it.
- **Don't read a whole large file** when `--peek` / `--skim` / `--range`
  can answer.
- **Don't blindly pick rank #1** when #1-#3 are clearly different objects.
- **Don't stop at one match** if the prompt mentions multiple entities.
- **Don't search the same vague words after a weak first round** — fix
  the query or escalate to literal anchors.
- **Don't `cat` a lazy object** (DB `rows.jsonl`, SaaS `records.jsonl`,
  chat `messages.jsonl`). Use `head`, `--range`, `--locator`, or `export`.
- **Don't use MFS for sources you'd just clone/download anyway** — pull
  locally and use the `file` connector.

## 12. When search returns nothing on a freshly indexed connector

This is the most common diagnostic case. Walk this ladder, stop on first
hit:

```bash
# 1. THIS connector's search availability
mfs status <uri>

# 2. failed sync jobs?
mfs job ls
mfs job logs <job-id>      # if any 'failed', read the cause

# 3. per-object granularity
mfs ls <uri> --json        # each entry's search_status

# 4. JSON error code path
# If --json output carried a `code` field, see reference/error-codes.md

# 5. otherwise treat as query-level — §9 above
```

| Signal | Meaning | Action |
|---|---|---|
| `building` | sync in flight | wait, or `mfs grep` until done |
| `partial` | chunks dropped (`chunk_max` / `max_read_rows`) | usable but incomplete; user may want to re-ingest with raised caps (→ redirect to `mfs-ingest`) |
| `unavailable` | nothing indexed | only `grep` / `ls` / `cat` work; redirect to `mfs-ingest` |
| `available` but smoke search empty | wrong `text_fields` / source empty / wrong scope | check `reference/connectors/<scheme>.md` for that connector's shape; `mfs cat` a known object to confirm content |

When the diagnosis points to "ingest config is wrong" or "needs re-sync
with different settings" — **don't try to fix it from this skill**. Tell
the user to invoke `mfs-ingest` for that connector.

## 13. Reference routing

These reference files are loaded ONLY when the situation matches — don't
open speculatively.

- **[`reference/json-envelope.md`](reference/json-envelope.md)** —
  WHEN parsing a `--json` search/grep result and the `locator` shape is
  unfamiliar (composite PKs, `thread_ts`, nested keys); OR when uncertain
  how to feed a hit back into `mfs cat` (range vs locator dispatch).

- **[`reference/error-codes.md`](reference/error-codes.md)** —
  WHEN an `mfs` command returned `--json` error output with a `code`
  field. Read the message first — don't open just because a command
  failed.

- **`reference/connectors/<scheme>.md`** —
  WHEN searching a specific connector and you need its tree shape, record
  field semantics, locator format, or search-strategy tips. STOP and read
  the matching one BEFORE guessing how that source enumerates objects
  or what fields its records carry. Schemes: `file`, `web`, `s3`,
  `gdrive`, `postgres`, `mysql`, `snowflake`, `bigquery`, `mongo`,
  `github`, `jira`, `linear`, `hubspot`, `salesforce`, `notion`,
  `zendesk`, `slack`, `discord`, `gmail`, `feishu`.

Runtime capability for a specific URI is queried structurally via
`mfs ls <uri> --json`; the static per-connector references describe what
the connector exposes by design.
