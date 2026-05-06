# MFS Command Reference

This reference focuses on command usage for agents. Prefer scoped commands over
global commands when the task gives a path. Use `--json` when another tool will
parse the output.

## Availability Check

Before using MFS, verify that the command is installed:

```bash
mfs --version
```

If the command is missing, install the published CLI package when setup is part
of the task:

```bash
uv tool install mfs-cli
```

The PyPI package name is `mfs-cli`; the installed command is `mfs`. If
installation is not appropriate, fall back to native shell tools and normal file
reads.

## Command Map

| Need | Command |
| --- | --- |
| Index files for semantic search | `mfs add` |
| Check queue/index readiness | `mfs status` |
| Natural-language or paraphrased search | `mfs search` |
| Exact string or regex search | `mfs grep` |
| Inspect one known file | `mfs cat` |
| Inspect one directory level | `mfs ls` |
| Inspect a directory tree | `mfs tree` |
| Remove indexed records | `mfs remove` |
| Configure providers, storage, cache | `mfs config` |

## Scope Rules

`mfs search` and `mfs grep` need an explicit scope in normal terminal use:

```bash
mfs search "<query>" <path>
mfs search "<query>" --all
mfs grep "<pattern>" <path>
mfs grep "<pattern>" --all
```

No path and no `--all` is an error. This avoids silently searching the wrong
corpus.

Pipe input is different:

- Headered `mfs cat` output scopes downstream `mfs search` to the source file.
- Plain non-empty stdin is searched as temporary text.
- Empty stdin returns empty results.

## `mfs add`

Use `add` when indexing is part of the task.

```bash
mfs add <path>
mfs add <path> --sync
mfs add <path> --force
mfs add <path> --watch
mfs add <path> --exclude "*.log"
mfs add <path> --quiet
```

Options:

| Option | Use |
| --- | --- |
| `--sync` | embed in the foreground and wait for completion |
| `--force` | skip the timestamp fast path and re-check file hashes |
| `--watch` | watch paths and re-index on file changes |
| `--interval 1500ms` | debounce watch mode; accepts `ms`, `s`, `m`, `h` |
| `--exclude <glob>` | exclude matching paths from this run |
| `--quiet` | reduce output |

Default behavior:

- Scans supported files under each path.
- Removes stale chunks for deleted files.
- Queues changed/new chunks into `$MFS_HOME/queue.json`.
- Starts a background worker.
- Returns before all embeddings are necessarily complete.

Use `--sync` when the next step depends on search results being ready:

```bash
mfs add ./docs --sync
mfs status
mfs search "refund policy" ./docs
```

Avoid starting a large indexing job unless the user asked for it or the task
clearly requires search over that folder. If unsure, run `mfs status` first or
ask.

### Summary and Image Description

Summaries and image descriptions are optional enrichment records. Normal
indexing does not require them.

```bash
mfs add ./docs --summarize
mfs add ./assets --describe
```

Options:

| Option | Use |
| --- | --- |
| `--summarize` | auto-generate summaries for indexed text files |
| `--describe` | auto-generate image descriptions with a vision-capable provider |

`--summarize` and `--describe` use the configured `[llm]` provider. Image
descriptions require a provider/model that can process images.

## `mfs status`

Use status to check whether background indexing is still running and whether
the index is ready enough to rely on.

```bash
mfs status
mfs status --json
mfs status --needs-summary
```

Output fields include queue size, worker state, indexed file/chunk counts, and
whether the Milvus backend appears busy.

Use `--needs-summary` when deciding whether `--summarize` should be run for
indexed files:

```bash
mfs status --needs-summary
mfs status --needs-summary --json
```

## `mfs search`

Use search for natural-language, conceptual, or paraphrased retrieval over
indexed content.

```bash
mfs search "<query>" <path>
mfs search "<query>" --all
mfs search "<query>" <path> --top-k 20
mfs search "<query>" <path> --mode hybrid
mfs search "<query>" <path> --mode semantic
mfs search "<query>" <path> --mode keyword
mfs search "<query>" <path> --json
mfs search "<query>" <path> --quiet
```

Modes:

| Mode | Use |
| --- | --- |
| `hybrid` | default; combines semantic and keyword signals |
| `semantic` | wording may differ from the files |
| `keyword` | exact terms, names, config keys, or error codes should matter |

Examples:

```bash
mfs search "how are pdf files converted and cached" . --top-k 10
mfs search "user cannot find payment history after manual payment" ./docs --top-k 20
mfs search "queue worker restores chunk text" ./src --mode semantic
mfs search "OPENAI_API_KEY" . --mode keyword
```

Behavior:

- Requires an existing index for the scoped files.
- Returns chunks with source paths, scores, snippets, and line ranges.
- Repeated hits from one file should be treated as one document-level
  candidate until verified.
- `--quiet` is useful for quick candidate lists.
- `--json` is useful for scripts or agents that need structured parsing.

After search, verify with browse:

```bash
mfs cat --peek -H 20 -D 3 <candidate-file>
mfs cat -n <start>:<end> <candidate-file>
```

## `mfs grep`

Use grep for exact text, regex-like patterns, identifiers, config keys, error
codes, URLs, and unique phrases.

```bash
mfs grep "<pattern>" <path>
mfs grep "<pattern>" --all
mfs grep -C 5 "<pattern>" <path>
mfs grep -i "<pattern>" <path>
mfs grep "<pattern>" <path> --json
mfs grep "<pattern>" <path> --quiet
```

Options:

| Option | Use |
| --- | --- |
| `-C N` | include context lines before and after each match |
| `-i` | case-insensitive matching |
| `--all` | search all indexed files |
| `--json` | structured output |
| `--quiet` | compact output |

Use native `grep` directly when the target is a small local directory or a
known small file set; there is no index lookup overhead, so it is often faster
for tiny scopes. Use `rg` if it is available and you want faster ripgrep
behavior. Use `mfs grep` when the scope is large, already indexed, or mixed
between indexed and non-indexed file types.

The difference is architectural: native `grep` scans file contents directly on
each run, while `mfs grep` first uses the existing Milvus keyword/BM25 index to
route to likely indexed files, then verifies exact line matches by reading
those files. That index-aware routing is why `mfs grep` can be much faster on
large indexed corpora, while plain `grep` can still win on small folders.

Examples:

```bash
mfs grep "ERR_TOKEN_EXPIRED" ./src
mfs grep -C 3 "cache.max_size_mb" .
mfs grep -i "manual payment" ./docs
mfs grep "def .*callback" ./src
```

Behavior:

- Indexed files use MFS routing and BM25 prefiltering before exact line checks.
- Readable non-indexed files under a concrete directory path can be scanned by
  system `grep` with a Python fallback.
- JSON, JSONL, CSV, YAML, TOML, HTML, XML, CSS, env files, and logs are not
  embedded by default but can be matched by `mfs grep`.

## `mfs cat`

Use cat to inspect a known file without reading more than needed.

```bash
mfs cat <file>
mfs cat --peek <file>
mfs cat --skim <file>
mfs cat --deep <file>
mfs cat -n 40:90 <file>
mfs cat --skim -H 12 -D 3 -W 160 <file>
mfs cat <file> --json
```

Density options:

| Option | Meaning |
| --- | --- |
| `--peek` | skeleton, headings, signatures, shape |
| `--skim` | compact overview with short excerpts |
| `--deep` | richer structured expansion |
| `-W N` | width: characters per node, paragraph, or value |
| `-H N` | height: number of top-level items |
| `-D N` | depth: structure levels to expand |
| `-n A:B` | exact source line range |

Display and pipe options:

| Option | Use |
| --- | --- |
| `--json` | emit structured output |
| `--no-frontmatter` | strip YAML frontmatter before display |
| `--meta` | force `::mfs:` metadata headers |
| `--no-meta` | omit `::mfs:` metadata headers |
| `--no-line-numbers` | omit source line numbers in density views |

Use `--peek` before reading a long unknown file:

```bash
mfs cat --peek -H 20 -D 3 ./docs/configuration.md
```

Use `-n A:B` for final evidence:

```bash
mfs cat -n 120:175 ./src/mfs/ingest/converter.py
```

Pipe examples:

```bash
mfs cat --meta ./docs/configuration.md | mfs search "zilliz cloud token"
mfs cat ./notes.txt | mfs search "follow-up task"
```

Headered MFS pipe output preserves source metadata. Plain text pipe input is
searched only as temporary stdin chunks.

PDF and DOCX files are converted to Markdown before browsing. JSON, JSONL, and
CSV have compact structured views.

## `mfs ls`

Use ls when you know a directory and need a bounded view of its immediate
children.

```bash
mfs ls <dir>
mfs ls --peek <dir>
mfs ls --skim <dir>
mfs ls --deep <dir>
mfs ls --skim -H 20 -D 2 -W 120 <dir>
mfs ls <dir> --json
```

Options:

| Option | Use |
| --- | --- |
| `--peek` | filenames and minimal structure |
| `--skim` | short summary per file; default style |
| `--deep` | richer summaries and deeper structure |
| `-W N` | width budget |
| `-H N` | item budget |
| `-D N` | expansion depth |
| `--json` | structured output |

Use `ls` to compare files in one directory after search has found a likely
scope. Do not use it as the only strategy for unknown conceptual targets.

## `mfs tree`

Use tree for an inexpensive map of a directory hierarchy.

```bash
mfs tree <dir>
mfs tree --peek -L 2 <dir>
mfs tree --skim -L 3 <dir>
mfs tree --deep -L 2 <dir>
mfs tree --skim -L 3 -H 20 -D 2 -W 120 <dir>
mfs tree <dir> --json
```

Options:

| Option | Use |
| --- | --- |
| `-L N` | maximum directory recursion depth |
| `--peek` | names only, no summaries |
| `--skim` | one-line summary per node; default style |
| `--deep` | richer per-node summaries |
| `-W N` | width budget |
| `-H N` | item budget per file |
| `-D N` | structure depth |
| `--json` | structured output |

Use tree to orient in an unfamiliar corpus or to inspect neighbors around a
candidate file:

```bash
mfs tree --peek -L 2 .
mfs tree --skim -L 2 ./docs
```

## `mfs remove`

Use remove when a file or directory should no longer appear in search results.
This removes index records; it does not delete source files.

```bash
mfs remove <file>
mfs remove <dir>
mfs remove <target> --quiet
```

Behavior:

- File targets delete records whose `source` matches that file.
- Directory targets delete indexed child records under that prefix and the
  directory summary record.
- Re-running `mfs add <path>` can recreate records for files that still exist.

## `mfs config`

Use config to inspect or change MFS settings.

```bash
mfs config path
mfs config init
mfs config init --force
mfs config show
mfs config show --json
mfs config get embedding.provider
mfs config set embedding.provider onnx
```

Config lives at:

```bash
~/.mfs/config.toml
```

`MFS_HOME` can move all MFS runtime state:

```bash
export MFS_HOME=/path/to/mfs-home
mfs config path
```

Common settings:

```bash
mfs config set embedding.provider onnx
mfs config set embedding.provider openai
mfs config set embedding.model text-embedding-3-large
mfs config set embedding.batch_size 32
mfs config set cache.max_size_mb 500
mfs config set milvus.collection_name mfs_chunks
```

Zilliz Cloud / remote Milvus example:

```bash
mfs config set milvus.uri "$ZILLIZ_URI"
mfs config set milvus.token "$ZILLIZ_TOKEN"
mfs config set milvus.collection_name mfs_chunks
```

API keys are usually better as environment variables than written into
`config.toml`:

```bash
export OPENAI_API_KEY="..."
export GOOGLE_API_KEY="..."
export VOYAGE_API_KEY="..."
export JINA_API_KEY="..."
export MISTRAL_API_KEY="..."
```

Before changing `milvus.collection_name` against shared Milvus or Zilliz Cloud,
inspect the environment and avoid reusing a collection that belongs to another
project.

## File Types

Indexed by default:

- Markdown, reStructuredText, plain text
- common source code and script extensions
- PDF and DOCX after Markdown conversion

Readable and grep-able, but not embedded by default:

- JSON, JSONL, CSV, TSV
- YAML, TOML, INI, env files
- HTML, XML, CSS, logs

Images can be searched only when text descriptions have been generated through
`--describe`.

Files larger than 10 MB are skipped by the scanner for indexing.
