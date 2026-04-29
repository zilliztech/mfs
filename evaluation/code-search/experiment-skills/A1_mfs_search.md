---
name: mfs
description: Semantic file search CLI for large codebases and document collections, powered by Milvus hybrid retrieval (dense vector + BM25). Complements agent shell tools (grep / find / cat) — pick whichever fits each sub-task.
---

# MFS — semantic file search (search lobe)

MFS is a CLI for searching across a pre-built index of your corpus. It does
NOT replace your agent shell tools — `grep`, `find`, `cat` remain useful for exact
matches, filename patterns, and reading known files. Use MFS when a
**semantic** angle pays off (paraphrased queries, conceptual matches).

## When to reach for MFS vs agent shell tools

| Sub-task | Reach for |
|---|---|
| Paraphrased / conceptual query ("rate limiting" when code uses "throttle") | `mfs search` |
| Exact identifier / error code | agent shell `Grep` (instant) or `mfs grep` |
| Read a known file | agent shell `Read` |
| Filename pattern (`**/*.py`) | agent shell `Glob` |
| Read specific lines of a file | agent shell `Read` offset/limit |

You typically only need ONE of {MFS, agent shell tools} per sub-task. If the result is
correct, that is the answer — do not re-verify with the other tool.

## Command reference

### `mfs search "<query>" [path] [--all] [--top-k N] [--mode hybrid|semantic|keyword]`

Hybrid retrieval against the pre-built index (dense vector + BM25 + RRF fusion).

- **No path, no `--all`, tty stdin**: **errors out** — no implicit cwd default.
- **`<path>` positional**: search files under that path (POSIX-style).
- **`--all`**: search the whole index (use when you don't know which subtree).
- **`--top-k N`**: number of results (default 10).
- **`--mode`**: `hybrid` (default) / `semantic` / `keyword`.

Each hit returns a ranked chunk with file path, line range, and the **actual
content at those lines**. Quoting text from a chunk is equivalent to reading
the file at those lines — no separate `cat` needed to confirm.

```
mfs search "OAuth flow" --all
mfs search "request validation" ./src/
mfs search "deprecated API" --top-k 20 --all
```

### `mfs grep <pattern> [path] [--all] [-C N] [-i]`

Indexed BM25 prefilter + exact regex on indexed files, with a system-grep
fallback on un-indexed files under the scope path.

- **No path, no `--all`, tty stdin**: **errors out**.
- **`<path>` positional**: matches Linux grep convention.
- **`--all`**: grep across the whole index.
- **`-C N`**: context lines before / after each match.
- **`-i`**: case insensitive.

```
mfs grep "ERR_TOKEN_EXPIRED" --all
mfs grep -C 3 "class.*Middleware" ./src/
mfs grep -i "todo" --all
```

## Common patterns (situations, not steps)

- **Returned chunk already contains the answer** → use it. The chunk body
  IS the file content at those lines. Do not re-read with native `Read`.
- **Empty result on first try** → was your scope right? Try `--all`, or
  rephrase (semantic retrieval is sensitive to phrasing; "auth" vs "login"
  vs "sign-in" hit different chunks).
- **Need surrounding context** → chunk gave L42-50 but the answer needs
  L30-80? Use native `Read` with offset/limit.
- **Running `mfs search "x"` from a tmp dir** → you must pass `--all` or a
  path. MFS does not silently default to cwd; it errors out.

## Common pitfalls

- Do **not** call `mfs search "x"` without `--all` or a path. It will error.
- Do **not** grep for a concept when `mfs search` is better (e.g. "rate
  limiting" matches "throttle", "backoff", "limiter" — grep for the word
  itself will miss them).
- Do **not** grep for a stable identifier when native `Grep` is right there
  and instant — use MFS only when the index adds value.

## Trust the chunk

`mfs search` already returns chunks with file path + line range + the actual
file content at those lines. **Do not** re-run `Grep` to "verify the file
exists" or "double-check the file really contains this passage" — the chunk
body IS the file content.

The experiment prompt included an instruction to avoid redundant verification:
if `mfs search` returns a chunk whose path, line range, and content answer the
question, the agent should answer from that evidence instead of running a
second grep only to prove the same passage exists.

## Output

End your reply with exactly one line, no text after it:

```
ANSWER: <relative/file/path | short answer>
```
