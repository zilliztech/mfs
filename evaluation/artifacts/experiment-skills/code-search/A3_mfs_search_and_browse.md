---
name: mfs
description: Semantic file search and progressive browsing CLI for large codebases and document collections, powered by Milvus hybrid retrieval (dense vector + BM25). Complements agent shell tools (grep / find / cat) — pick whichever fits each sub-task.
---

# MFS — semantic file search and progressive browsing

MFS is a CLI for navigating large indexed corpora. It does NOT replace your
agent shell tools — `Grep`, `Glob`, `Read` remain useful for exact matches,
filename patterns, and reading known files. Use MFS when **semantic** angles
pay off, and use MFS browse tools only as a **magnifier on top of search**
(see decision tree below).

## Decision tree — pick exactly ONE per sub-task

START: what are you doing?

**(1) Find function / concept by natural-language description**
→ `mfs search "<query>" --all --top-k 10`
→ Read the top-1 chunk. It contains the file path + line range + the actual
  content at those lines.
→ That is usually the answer. **ANSWER directly. Do not grep to verify.**

**(2) Search for an exact identifier / error code / literal string**
→ native `Grep`. Do not use MFS for this.

**(3) Known file X, want its function list / overall structure**
→ `mfs cat X --peek`

**(4) Search returned candidate X but the chunk window is too narrow — want
more surrounding context**
→ `mfs cat X -n A:B` (use the chunk's line range ±20)

**(5) Filename pattern (`*.py`, `**/*test*`)**
→ native `Glob`. Do not use MFS.

**(6) `mfs search` already ran, but top-1 looks off-topic / candidates weak
(no clear semantic match)**
→ First: **paraphrase the query with synonyms** and re-run `mfs search`.
  (e.g. "retry with backoff" got nothing → try "exponential delay",
  "throttle", "rate limit"; "auth" → "login" / "sign-in" / "credential".)
→ Second: if paraphrasing still doesn't land, `mfs cat <candidate> --peek`
  on top-2 / top-3 candidates and pick the one whose skeleton best matches
  intent.
→ **Do NOT default-fallback to `bash grep`.** If `mfs search` could not
  find the concept, `grep` on the same literal words will find it even
  less — grep is only right for the *exact literal-match* case in step (2).

### Anti-patterns (stop the moment you notice)

- ✗ `mfs search` already returned a candidate → then running `grep` to "verify
  it exists". **The chunk body IS the file content.** There is nothing to
  verify.
- ✗ First action is `mfs ls` / `mfs tree` to explore the corpus from cold.
  Use `mfs search` — do not wander.
- ✗ Running BOTH `mfs search` and `grep` for the same question. Pick one,
  commit.
- ✗ Re-reading a file with `mfs cat` / `Read` on a line range that a search
  chunk already returned.
- ✗ **`mfs search` looked unsuccessful → immediately pivoting to `grep`
  with keyword combinations.** Paraphrase + re-search first, or `mfs cat
  --peek` the top candidates. Grep is not smarter than search at concept
  matching; it is only better at literal identifiers.

## mfs cat / ls / tree are NOT for finding files

They are the magnifier that sits on top of search, not an independent way of
locating files:

- `mfs search` returned chunk L42-50 but you want L20-80 → `mfs cat -n 20:80 <file>`
- `mfs search` hit file X, want to see what else is inside X → `mfs cat X --peek`
- `mfs search` hit directory X/, want to peek at sibling files → `mfs ls X/`

If you **do not yet have a candidate**, do not `cat` / `ls` / `tree` first.
Run `mfs search`. Cold-start navigation with `mfs ls` / `mfs tree` is an
anti-pattern — the agent burns turns exploring and arrives slower.

## Command reference (how to run, not when to run)

The decision tree above says *when* to use each tool. This section only
documents *how* to invoke them.

### `mfs search "<query>" [path] [--all] [--top-k N] [--mode hybrid|semantic|keyword]`

Hybrid retrieval (dense vector + BM25 + RRF fusion). Returns ranked chunks
with file path, line range, and the actual content at those lines.

- No path + no `--all` + tty stdin → **errors out**.
- `<path>` positional: scope to subtree.
- `--all`: search the whole index.
- `--top-k N`: default 10.
- `--mode`: `hybrid` (default) / `semantic` / `keyword`.

```
mfs search "OAuth flow" --all
mfs search "request validation" ./src/
mfs search "deprecated API" --top-k 20 --all
```

### `mfs grep <pattern> [path] [--all] [-C N] [-i]`

Indexed BM25 prefilter + exact regex, falls back to system grep on
un-indexed files under scope.

- No path + no `--all` + tty stdin → **errors out**.
- `-C N`: context lines.
- `-i`: case insensitive.

```
mfs grep "ERR_TOKEN_EXPIRED" --all
mfs grep -C 3 "class.*Middleware" ./src/
```

### `mfs cat <file> [-n A:B] [--peek | --skim | --deep] [-W N] [-H N] [-D N]`

| Mode | What you get |
|---|---|
| (no flag) | full content |
| `-n A:B` | lines A through B |
| `--peek` | heading / signature skeleton |
| `--skim` | headings + first paragraph per section |
| `--deep` | expanded structure with most body |

`-W N` = max chars per node, `-H N` = max nodes, `-D N` = max heading depth.

```
mfs cat --peek ./docs/auth.md
mfs cat -n 40:80 ./src/handler.py
mfs cat ./docs/auth.md -W 200 -H 10 -D 3
```

### `mfs ls <dir>` and `mfs tree <dir> [-L N]`

Directory listing with per-file auto-summary. Accept `--peek / --skim /
--deep` and `-W / -H / -D`.

```
mfs ls ./docs/
mfs tree --peek -L 2 .
```

### Pipe optimization — `mfs cat | mfs search`

`mfs cat <file>` writes a `::mfs:` metadata header when stdout is piped.
Downstream `mfs search` / `mfs grep` scopes to that file's chunks without
re-embedding.

```
mfs cat ./docs/auth.md | mfs search "token refresh"
```

## Output

End your reply with exactly one line, no text after it:

```
ANSWER: <relative/file/path | short answer>
```
