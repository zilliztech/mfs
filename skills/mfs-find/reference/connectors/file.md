# file connector — search & browse

## URI tree

```
file://<alias>/...                  ← mirrors the on-disk tree
```

Or just bare paths: `mfs search "X" /abs/path` resolves to
`file://local/abs/path` automatically.

## Content kinds & conversion

| Source | Routing | Chunk kind |
|---|---|---|
| Markdown / plain text | chonkie RecursiveChunker | `chunk_body` |
| Source code (Python / JS / TS / Go / Rust / Java / …) | chonkie CodeChunker (AST-aware) | `chunk_body` |
| PDF | markitdown → md → chunked | `chunk_body` (+ `converted_md` artifact cached) |
| Office (`.docx`/`.xlsx`/`.pptx`) | markitdown | `chunk_body` |
| Images (`.png`/`.jpg`/...) | VLM if `summary.enabled` | `vlm_description` |
| Binary (no media handler) | enumerated, NOT indexed | — |

Dir summaries (if `[summary] enabled = true`) → `directory_summary`.

## Locator

`{"lines": [s, e]}` for body chunks. Same for code chunks (chonkie
gives back line ranges).

## Search strategy

| Intent | Use |
|---|---|
| "Find X in this codebase" | `mfs search "X" /repo/path` (file://local/...) |
| Concept query that won't match literally | `mfs search "X" /path --mode semantic` |
| Exact identifier / function name | `mfs grep "X" /path` (faster + literal-exact) OR plain `rg` |
| Outline of one file | `mfs cat /path/file.py --peek` |
| Section of one file by line | `mfs cat /path/file.py --range 100:150` |

## When to use file:// vs raw `rg` / `grep`

- **Big repo + semantic intent**: file:// wins ("rate-limiting strategy"
  semantic match).
- **Small repo + literal**: `rg` is faster + no setup. file:// is overkill.
- **Cross-source**: file:// is part of a larger MFS namespace; if the
  user wants "ANY past mention across slack + jira + this repo", that's
  MFS's lane.

## Pitfalls

- **`.gitignore` / `.mfsignore` respected**: files matching either are
  silently skipped. `mfs ls` won't show them. If "where's my .env" —
  it's hidden by gitignore.
- **`max_file_bytes` cap**: big files (logs, datasets) skip silently.
  `mfs ls --json` shows `search_status: not_indexable` for these.
- **Symlinks followed**: tracked by absolute realpath; cycles broken
  by the native walker.
- **Tree changes between syncs**: full-scan every time. New files
  appear, deleted files removed.
