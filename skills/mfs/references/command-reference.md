# MFS Command Reference

This reference focuses on command usage for agents. Prefer scoped commands over
global commands when the task gives a path.

## `mfs search`

Use semantic, keyword, or hybrid retrieval over indexed files.

```bash
mfs search "<query>" <path>
mfs search "<query>" --all
mfs search "<query>" <path> --top-k 20
mfs search "<query>" <path> --mode hybrid
mfs search "<query>" <path> --mode semantic
mfs search "<query>" <path> --mode keyword
mfs search "<query>" <path> --json
```

Rules:

- `hybrid` is the default and usually the best first choice.
- `semantic` helps when wording differs from the files.
- `keyword` helps with important literals that should remain exact-ish.
- No path and no `--all` errors in normal terminal use.
- Plain stdin is searched as temporary text.
- Headered `mfs cat` output scopes downstream search to the original source.

Example:

```bash
mfs search "how are pdf files converted and cached" . --top-k 10
```

## `mfs grep`

Use exact text or regex search with MFS routing.

```bash
mfs grep "<pattern>" <path>
mfs grep "<pattern>" --all
mfs grep -C 5 "<pattern>" <path>
mfs grep -i "<pattern>" <path>
mfs grep "<pattern>" <path> --json
```

Use native `grep` directly when the target is clearly a literal search over
local files. Use `rg` if it is available and you want faster ripgrep behavior.
Use `mfs grep` when you want one interface across indexed and non-indexed
files.

## `mfs cat`

Use `cat` to inspect a known file at a controlled density.

```bash
mfs cat <file>
mfs cat --peek <file>
mfs cat --skim <file>
mfs cat --deep <file>
mfs cat -n 40:90 <file>
mfs cat --skim -H 12 -D 3 -W 160 <file>
```

Density options:

| Option | Meaning |
| --- | --- |
| `--peek` | skeleton, headings, signatures, shape |
| `--skim` | compact overview with short excerpts |
| `--deep` | richer structured expansion |
| `-W` | width: characters per node or excerpt |
| `-H` | height: number of top-level items |
| `-D` | depth: structure levels to expand |
| `-n A:B` | exact source line range |

Use `-n A:B` for final verification before citing, editing, or choosing a
candidate.

## `mfs ls`

Use `ls` when you know a directory and need a compact view of its children.

```bash
mfs ls <dir>
mfs ls --peek <dir>
mfs ls --skim <dir>
mfs ls --deep <dir>
```

`ls` accepts `-W`, `-H`, `-D`, and `--json`.

## `mfs tree`

Use `tree` for an inexpensive map of a directory hierarchy.

```bash
mfs tree --peek -L 2 <dir>
mfs tree --skim -L 3 <dir>
mfs tree --deep -L 2 <dir>
```

`-L` controls directory recursion depth. `tree` also accepts `-W`, `-H`, `-D`,
and `--json`.

## `mfs status`

Use status to check whether background indexing is still running.

```bash
mfs status
mfs status --json
mfs status --needs-summary
```

## `mfs add`

Use add when indexing is part of the task.

```bash
mfs add <path>
mfs add <path> --sync
mfs add <path> --force
mfs add <path> --watch
```

Default `mfs add` queues embedding work and starts a background worker. `--sync`
runs embedding in the foreground.

Do not run large indexing jobs casually when the user only asked a question and
the index state is unknown. Check status or ask.

## File Types

Indexed by default:

- Markdown, reStructuredText, plain text
- common source code and script extensions
- PDF and DOCX after Markdown conversion

Readable and grep-able, but not embedded by default:

- JSON, JSONL, CSV, TSV
- YAML, TOML, INI, env files
- HTML, XML, CSS, logs

Images can be searched only when text descriptions have been added.
