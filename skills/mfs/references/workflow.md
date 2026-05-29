# Workflow: search → locate → browse (for large collections)

MFS shines on **large, indexed** collections. The index makes semantic + keyword
search fast and high-recall, so you don't scan everything — you search to find
*where*, then read the exact range to confirm *what*, then browse nearby only if
needed.

For a **small** collection or an exact string in a known file, this loop is
overkill — just `grep`/`rg`/read. MFS's index adds little there.

## The loop

1. **Search for candidates** — the index does the heavy lifting:
   ```bash
   mfs search "<what the user actually wants>" <path-or-uri> --top-k 10
   ```
   - default `hybrid` = dense (meaning) + BM25 (keywords), fused with RRF — best general default.
   - `--mode semantic` when the query is conceptual and wording won't match literally.
   - `--mode keyword` when you want BM25 term matching over the index.
   - `--all` to search every registered connector; scope to a `<path>` to stay focused.

2. **Locate the exact spot** from each result's `locator` (see json-envelope.md):
   - **text / code** hit → `locator: {"lines":[start,end]}`:
     ```bash
     mfs cat <source> --range <start>:<end>
     ```
   - **structured** hit (DB row, issue, slack thread) → `locator: {<pk>}`:
     ```bash
     mfs cat <source> --locator '{"id":12}'      # exact single record (flat, keyed by locator_fields)
     ```
     Pass the locator back verbatim; it reopens the precise unit.
   - **once-per-object** chunk (dir summary, image VLM) → `locator: null`:
     `mfs cat <source>` is enough.

3. **Browse nearby** to verify context before answering/editing:
   ```bash
   mfs cat --peek <file>     # heading/symbol skeleton (document/code only)
   mfs cat --skim <file>     # + one-line summaries
   mfs head -n 20 <uri>      # first records of a structured object
   mfs tree <uri> -L 2       # structure of a subtree
   ```

## Weak results → recover, don't thrash

If top hits look off:
1. Rewrite the query with domain synonyms / more specific terms.
2. Raise `--top-k` to compare more distinct candidates.
3. `mfs cat --peek` the top few to compare structure.
4. Switch to literal `mfs grep` / `grep` only if the task has a literal anchor
   (error code, identifier). Literal search is a *different* tool, not a stronger
   version of semantic search — don't just grep the same vague words.

## Scoping

```bash
mfs search "<q>" ./src --top-k 10            # local dir
mfs search "<q>" postgres://prod/public/tickets   # one connector subtree
mfs search "<q>" --all --top-k 20            # everything indexed
```

## Exact lookups & big objects

- Exact identifier / error code / config key → `mfs grep "<literal>" <path>` (or `grep`/`rg`).
- Single record by key → `mfs cat <source> --locator '{...}'`.
- Whole large object for offline analysis → `mfs export <uri> <file>` then `jq`/`grep` locally
  (don't `cat` a huge object — it's refused; use `--range`/`head`/`export`).

## Not-yet-indexed / progressive availability

`mfs add` is async. Check readiness before trusting search:
```bash
mfs status <uri>        # search: available | partial | building | unavailable
mfs ls <uri> --json     # per-object search_status: indexed | partial | building | stale | not_indexed
```
While `building`, fall back to `grep` / `ls` / `cat`; switch to `search` once `available`.
