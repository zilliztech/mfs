# Search and Browse

Use MFS as a daily search-to-read loop:

```text
search candidates -> locate exact evidence -> browse/read narrowly
```

The important rule is that search results are starting points, not evidence.
Always reopen the result with `cat`, `head`, `tail`, or `export` before you
quote, summarize, edit, or make a decision.

Related references:

- [CLI](cli.md) for the full command surface.
- [HTTP API](api.md) for direct `/v1` integration.
- [Connectors](connectors.md) for source-specific reference pages.
- [Content Model](content-model.md) for `source`, `locator`,
  `metadata.fields`, chunk kinds, and search status vocabulary.
- [Providers and Processing](providers.md) for embedding, summary, VLM, and
  converter behavior behind indexing and semantic search.
- [Troubleshooting](troubleshooting.md) for ingest and indexing failures.
- [Error Codes](errors.md) for the full code-to-recovery matrix.

## Mental Model

| Step | Goal | Primary command |
|---|---|---|
| Search | Find likely files, rows, threads, or records from indexed content. | `mfs search` |
| Locate | Capture `source` plus `locator` from JSON results so you can reopen the exact hit. | `mfs search --json` or `mfs grep --json` |
| Browse | Read only the relevant object, range, record, or neighborhood. | `mfs cat`, `mfs head`, `mfs tail`, `mfs ls`, `mfs tree` |

Use paths exactly as MFS returns them. A `source` from search is an object URI
that can be fed back to `cat`, `head`, `tail`, or `export`.

## Command Decision Table

| Need | Command | Flags that matter | Use it when |
|---|---|---|---|
| Find candidates by meaning or mixed semantic/keyword signal | `mfs search QUERY PATH` | `--mode hybrid`, `--mode semantic`, `--mode keyword`, `--top-k N`, `--kind kind1,kind2`, `--collapse` | You know what you need, but not the exact file, row, or wording. |
| Search every registered source | `mfs search QUERY --all` | Same search flags | You do not know which source contains the answer. Use this sparingly; scoped paths are easier to verify. |
| Find an exact literal pattern | `mfs grep PATTERN PATH` | `--json` | The spelling matters, or search results are weak while browse still works. |
| List one directory/object container | `mfs ls PATH` | `--json` | You need child paths, object types, media hints, and per-entry index state. |
| Walk a subtree | `mfs tree PATH -L N` | `-L N`, `--depth N` | You need orientation before narrowing to one object. |
| Read an object | `mfs cat PATH` | `--range A:B`, `--locator JSON`, `--meta`, `--peek`, `--skim` | You have a candidate source and need exact evidence. |
| Reopen text/code hits | `mfs cat SOURCE --range A:B` | `--locator '{"lines":[A,B]}'` also works | Search or grep returned a line locator. |
| Reopen structured hits | `mfs cat SOURCE --locator JSON` | Pass the locator JSON back verbatim | Search returned a database row, issue, message, or other structured-record locator. |
| Read only the start or end | `mfs head PATH -n N`, `mfs tail PATH -n N` | `-n N`, `--lines N` | The object is large, or you need a quick shape check. |
| Save full content outside the prompt | `mfs export PATH OUT` | none | `cat` is too large, or you need the whole object in a local file. |
| Keep machine-readable output | `mfs --json <command> ...` | global `--json` | You need `source`, `locator`, `metadata`, `via`, `search_status`, or raw API fields. |

`--json` is global. Current search, grep, `ls`, tree, cat, head, tail, and
`mfs job list` commands print raw JSON when it is present. `mfs status` already
prints JSON. `mfs export PATH OUT` writes the full object to `OUT` and prints a
short confirmation.

## Search

`mfs search` queries indexed chunks. The CLI requires either a scoped `PATH` or
`--all`:

```bash
mfs search "rate limit handler" ./server --top-k 10
mfs search "where is the retry budget documented" --all --top-k 20
```

Search modes:

| Mode | Command | Behavior |
|---|---|---|
| Hybrid | `mfs search "query" PATH --mode hybrid` | Default. Combines semantic and keyword retrieval. |
| Semantic | `mfs search "query" PATH --mode semantic` | Uses embedding similarity for wording that may not match exactly. |
| Keyword | `mfs search "query" PATH --mode keyword` | Uses the sparse/BM25 path for exact terms, identifiers, and names. |

Useful search flags:

| Flag | Example | Effect |
|---|---|---|
| `--top-k` | `--top-k 25` | Ask for more candidates before giving up on the query. |
| `--kind` | `--kind row_text,schema_summary` | Restrict search to comma-separated chunk kinds. |
| `--collapse` | `--collapse` | Keep only the first hit per `source` object when many chunks from one object dominate results. |
| `--all` | `mfs search "quota" --all` | Search the whole namespace instead of one path. |

Start scoped whenever possible. A good daily loop is:

```bash
mfs search "connector auth config" ./server --top-k 5 --json
mfs cat file://local/path/to/repo/server/python/src/mfs_server/server/connector_schemas.py --range 1:120
```

## Locate Exact Hits

Use `--json` when you intend to reopen evidence. Search returns:

```json
{
  "results": [
    {
      "source": "file://local/path/to/repo/src/throttle.go",
      "content": "func retryWithBudget(ctx context.Context, budget Budget) { ...",
      "score": 0.82,
      "locator": {"lines": [42, 78]},
      "metadata": {
        "kind": "search",
        "chunk_kind": "body",
        "fields": {}
      }
    }
  ]
}
```

| Field | Meaning | What to do with it |
|---|---|---|
| `source` | Object URI. | Feed it to `cat`, `head`, `tail`, or `export`. |
| `content` | Snippet from the hit. | Use only as a preview; reopen before relying on it. |
| `score` | Ranking score, when available. | Treat very low scores as weak candidates. |
| `locator` | Per-hit identity. | Reopen with `cat --range` or `cat --locator`. |
| `metadata.chunk_kind` | Chunk type such as `body`, `row_text`, `schema_summary`, `thread_aggregate`, or other indexed kinds. | Use with `--kind` to narrow later searches. |
| `metadata.fields` | Connector-provided business fields. | Use for quick filtering before opening the object. |

Grep JSON is smaller:

```json
{
  "results": [
    {
      "source": "file://local/path/to/repo/src/throttle.go",
      "locator": {"lines": [44, 44]},
      "content": "retryWithBudget(ctx, budget)",
      "via": "linear"
    }
  ]
}
```

`via` can identify how grep found the match, for example pushdown, BM25, linear
scan, or a notice.

## Reopen File-Like Hits

Text, code, and document chunks use a line locator:

```json
{"lines": [42, 78]}
```

Reopen with either form:

```bash
mfs cat file://local/path/to/repo/src/throttle.go --range 42:78
mfs cat file://local/path/to/repo/src/throttle.go --locator '{"lines":[42,78]}'
```

`cat --range` also accepts open-ended forms:

```bash
mfs cat ./logs/app.log --range 200:
mfs cat ./logs/app.log --range :80
mfs cat ./logs/app.log --range 120
```

A lone start value such as `120` is treated like `120:`.

For large objects, avoid bare `cat`:

```bash
mfs head ./logs/app.log -n 40
mfs tail ./logs/app.log --lines 80
mfs export ./logs/app.log /tmp/app.log
```

## Reopen Structured Hits

Structured connectors return locator dictionaries instead of line ranges. Do
not infer the key names. Copy the `locator` JSON from the hit and pass it back
to `cat`.

Example structured search hit:

```json
{
  "source": "postgres://prod/public/tickets/rows.jsonl",
  "content": "title: Login broken after SSO migration\nstatus: open",
  "score": 0.84,
  "locator": {"id": 12345},
  "metadata": {
    "kind": "search",
    "chunk_kind": "row_text",
    "fields": {"status": "open", "priority": "high"}
  }
}
```

Reopen the exact row:

```bash
mfs cat postgres://prod/public/tickets/rows.jsonl --locator '{"id":12345}'
```

Composite locators are also passed verbatim:

```bash
mfs cat postgres://prod/public/memberships/rows.jsonl --locator '{"org_id":7,"user_id":999}'
```

Other structured locator shapes can include issue numbers, message IDs, or
thread keys, depending on the connector and object. The daily rule is the same:
copy `locator` from the JSON hit; do not rewrite it from memory.

## Browse When Search Is Weak

Search depends on indexed chunks. Browse commands do not. Grep sits between
them: it can use connector pushdown when available, BM25 over indexed content,
and a bounded linear scan over `not_indexed` objects in scope.

Use `mfs ls PATH --json` to inspect per-entry state:

```bash
mfs ls ./server --json
```

Current `ls` entries expose `search_status` as `indexed`, `partial`,
`not_indexed`, or `null` when the object has not been recorded. The error
references also define source-level search availability values: `available`,
`partial`, `building`, and `unavailable`. Treat those as source-level
availability when a status surface reports them, not as `ls` entry values.

| State | What it means for search | What to do |
|---|---|---|
| `available` or `indexed` | Search should be usable for that source/object. | Use `mfs search`, then reopen with `cat --range` or `cat --locator`. |
| `partial` | Search can return results, but recall may be incomplete. | Increase `--top-k`, use `--collapse`, verify with `grep`, and browse exact paths. |
| `building` | Indexing is in progress. Results may be missing or changing. | Use `mfs grep PATTERN PATH`, `mfs ls`, `mfs tree`, `mfs cat`, and check `mfs job list`. |
| `unavailable` | No searchable index is available for that source. | Use `grep`, `ls`, `tree`, `cat`, `head`, `tail`, or `export`; fix ingest separately. |
| `not_indexed` | The object is known but has no chunks. Search will not find it. | Use `grep` for exact terms and `cat`/`head`/`tail` for direct reading. Narrow the path if grep returns a linear-scan notice. |
| `null` | The entry is visible from the source but has no object metadata row. | Browse it directly, then check ingest or path scope if it should be searchable. |

Check indexing work:

```bash
mfs status
mfs job list
mfs job show <job_id>
mfs job cancel <job_id>
```

## Weak-Result Recovery

| Symptom | Try this | Why |
|---|---|---|
| No candidates | `mfs search "query" PATH --top-k 25` | The right hit may be below the default top 10. |
| Search is too fuzzy | `mfs search "exact identifier" PATH --mode keyword` | Keyword mode favors literal terms. |
| Exact token missing | `mfs grep "ExactToken" PATH --json` | Grep is the right tool for exact strings. |
| One file/object dominates results | `mfs search "query" PATH --collapse` | Collapse keeps one hit per source object. |
| Wrong chunk type | `mfs search "query" PATH --kind schema_summary` | Kind filters remove irrelevant chunks. Use the chunk kinds shown in prior JSON hits or connector docs. |
| Scope is too broad | `mfs tree PATH -L 3`, then search a child path | Narrow paths reduce noise and make verification faster. |
| Hit preview looks right but evidence is unclear | `mfs search "query" PATH --json`, then `mfs cat SOURCE --locator JSON` | Locator reopens the exact text range or structured record. |
| Bare `cat` fails on size | `mfs head PATH -n 40`, `mfs cat PATH --range A:B`, or `mfs export PATH OUT` | These avoid reading a huge object into the prompt. |
| `cat --locator` returns `locator_not_found` | Re-run search and use the new locator. | Structured records can change after indexing. |
| `ls --json` shows `not_indexed` | `mfs grep "term" PATH`, `mfs head PATH -n 20`, or `mfs cat PATH --range A:B` | Search cannot find chunks that do not exist. |
| Status is `building` | `mfs job list`, `mfs job show <job_id>`, and browse with `grep`/`cat` meanwhile | The index is still changing. |

## Error Recovery

When commands fail, JSON errors use a stable `code`, human `detail`, and
optional `suggestions`. Act on `code` first. See
[Error Codes](errors.md) for the full matrix.

| Code | Usually means | Recovery |
|---|---|---|
| `object_too_large_for_cat` | Bare `cat` would read too much. | Use `head`, `cat --range`, or `export`. |
| `is_directory` | You tried to `cat` a directory/container. | Use `ls` or `tree`. |
| `range_unsupported` | The object cannot serve that range. | Use `cat --meta` or `export`. |
| `density_unsupported` | `--peek` or `--skim` was used on a structured object. | Use `head` or `cat --range`. |
| `locator_not_found` | The structured record key is not present now. | Re-search and pass the fresh locator. |
| `not_found` | Path, object, or job was not found. | Check the URI from `source`, `path`, or the job ID. |

`--peek` and `--skim` are density views for document/code-style objects:

```bash
mfs cat ./docs/architecture.md --peek
mfs cat ./docs/architecture.md --skim
```

For structured records, prefer `head`, `cat --range`, or `cat --locator`.
