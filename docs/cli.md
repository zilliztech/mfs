# CLI Reference

MFS provides one command-line entry point:

```bash
mfs [OPTIONS] COMMAND [ARGS]...
```

Most commands accept local file-system paths. Search commands read from the
Milvus-backed index, while browse commands can inspect the live file system even
before a path has been indexed.

## Command Groups

| Group | Commands | Purpose |
| --- | --- | --- |
| Index | `add`, `remove`, `status` | Build, update, inspect, or delete indexed records. |
| Search | `search`, `grep` | Find candidate files with semantic, hybrid, keyword, or exact matching. |
| Browse | `cat`, `ls`, `tree` | Inspect files and directories with controllable context budgets. |
| Configuration | `config path`, `config init`, `config show`, `config get`, `config set` | Manage `~/.mfs/config.toml`. |

## Global Options

```bash
mfs --version
mfs --help
mfs <command> --help
```

| Option | Output |
| --- | --- |
| `--version` | Prints the installed package version. |
| `--help` | Prints usage, options, and subcommands. |

## Index Commands

### `mfs add`

Index local files and directories.

```bash
mfs add PATHS... [OPTIONS]
```

Inputs:

| Input | Meaning |
| --- | --- |
| `PATHS...` | One or more existing files or directories. Directories are scanned recursively. |

Options:

| Option | Meaning |
| --- | --- |
| `--exclude TEXT` | Glob pattern to skip. Can be repeated. Applies in addition to ignore files and built-in ignored directories. |
| `--force` | Recompute hashes even when the stored mtime shortcut says a file is unchanged. |
| `--watch` | Keep watching the target paths and re-index changes. |
| `--interval TEXT` | Debounce interval for watch mode, such as `1500ms`, `10s`, or `1m`. |
| `--summarize` | Auto-generate file-level summaries for indexed text-like files using the configured `[llm]` provider. PDF and DOCX are converted to Markdown first. |
| `--describe` | Auto-generate searchable image descriptions for PNG, JPG/JPEG, GIF, WEBP, and BMP using a vision-capable provider. |
| `--sync` | Run embedding and writes in the foreground. Without this option, tasks are queued and a detached worker processes them. |
| `--quiet` | Suppress progress text. |

Output:

- In normal async mode, prints a summary of scanned files and queued chunks,
  then starts the background worker.
- With `--sync`, blocks until embedding and Milvus writes finish.
- With `--quiet`, prints nothing unless an error occurs.

Example:

```bash
mfs add ./docs ./src --exclude "node_modules/**" --sync
```

Typical output:

```text
Indexed: 3 files scanned, 3 touched, 0 deleted, 4 chunks queued.
Embedding complete.
```

Notes:

- The async queue stores lightweight references for body chunks and generated
  summaries; it does not store raw body text.
- `--summarize` and `--describe` are opt-in. Normal indexing does not call an
  LLM or VLM.
- Directory summary records are rebuilt after indexing so `mfs ls`, `mfs tree`,
  and directory search results can show compact overviews.

### `mfs status`

Show index and worker status.

```bash
mfs status [OPTIONS]
```

Options:

| Option | Meaning |
| --- | --- |
| `--json` | Emit machine-readable status. |
| `--needs-summary` | List indexed files that do not have a fresh generated summary. |

Text output includes:

- current worker state
- indexed file count
- total, complete, and pending chunk count
- directory summary count
- queued task count, when tasks are waiting
- last sync timestamps by root

Example:

```bash
mfs status
```

Typical output:

```text
State: idle
Indexed files: 3
Chunks: 4 total  (4 complete, 0 pending)
Directory summaries: 3
Processed this session: 4
Last sync:
  /repo/demo: 2026-04-30 08:03:33 UTC
```

JSON output is intended for scripts:

```bash
mfs status --json
```

### `mfs remove`

Remove indexed records for a file or directory.

```bash
mfs remove TARGET [--quiet]
```

Inputs:

| Input | Meaning |
| --- | --- |
| `TARGET` | File or directory path. Directory paths remove indexed records under that prefix. |

Options:

| Option | Meaning |
| --- | --- |
| `--quiet` | Suppress success text. |

Output:

```text
Removed 12 indexed chunks for /repo/docs
```

## Search Commands

### `mfs search`

Search indexed files using semantic, keyword, or hybrid retrieval.

```bash
mfs search QUERY [PATH] [OPTIONS]
```

Inputs:

| Input | Meaning |
| --- | --- |
| `QUERY` | Natural-language query or keyword query. |
| `PATH` | Optional file or directory scope. If omitted, use `--all` or pipe scoped MFS output into stdin. |

Options:

| Option | Meaning |
| --- | --- |
| `--top-k INTEGER` | Number of results to return. Default: `10`. |
| `--path TEXT` | Compatibility alias for the positional `PATH`. |
| `--all` | Search across all indexed files. |
| `--mode hybrid` | Default. Combine dense semantic ranking and keyword/BM25 ranking. |
| `--mode semantic` | Dense-vector semantic retrieval only. Useful for paraphrased queries. |
| `--mode keyword` | Keyword/BM25 retrieval only. Useful for exact terms and identifiers. |
| `--json` | Emit structured result objects. |
| `--quiet` | Print one compact line per result, without snippets. |

Text output:

```bash
mfs search "how are refresh tokens revoked" ./demo --top-k 2
```

```text
[1] /repo/demo/README.md  score=0.033

  5  ## Operations
  6
  7  Refresh tokens rotate every seven days. The background cleanup job is called Cedar Sweep.

[2] /repo/demo/docs/runbook.md  score=0.032

  1  # Runbook
  2
  3  Use Cedar Sweep when a refresh token must be revoked across all devices.
```

JSON output:

```bash
mfs search "Cedar Sweep" ./demo --top-k 1 --json
```

```json
[
  {
    "source": "/repo/demo/docs/runbook.md",
    "lines": [1, 4],
    "content": "# Runbook\n\nUse Cedar Sweep when a refresh token must be revoked across all devices.",
    "score": 0.0325,
    "metadata": {
      "kind": "search",
      "content_type": "markdown",
      "is_dir": false,
      "chunk_index": 0
    }
  }
]
```

Stdin behavior:

- Piped `mfs cat` output with `::mfs:` headers scopes search to the original
  source file.
- Plain stdin is searched as temporary text and may return an empty result if it
  does not map to indexed files.

### `mfs grep`

Run full-text search with index-aware routing.

```bash
mfs grep PATTERN [PATH] [OPTIONS]
```

Inputs:

| Input | Meaning |
| --- | --- |
| `PATTERN` | Literal or regex-like text pattern. |
| `PATH` | Optional file or directory scope. Use `--all` to skip scope checks. |

Options:

| Option | Meaning |
| --- | --- |
| `--path TEXT` | Compatibility alias for positional `PATH`. |
| `--all` | Search across all indexed files. |
| `-C INTEGER` | Include context lines before and after each match. |
| `-n` | Accepted for compatibility. Line numbers are always shown in the gutter. |
| `-i` | Case-insensitive search. |
| `--json` | Emit structured result objects. |
| `--quiet` | Print compact output. |

Example:

```bash
mfs grep "Cedar Sweep" ./demo -C 1
```

```text
/repo/demo/README.md
  2
  3  This project documents token rotation, Cedar Sweep revocation, and search examples.
  4
--
  6
  7  Refresh tokens rotate every seven days. The background cleanup job is called Cedar Sweep.

/repo/demo/docs/runbook.md
  2
  3  Use Cedar Sweep when a refresh token must be revoked across all devices.
```

When to use `mfs grep` versus shell `grep`:

- `mfs grep` is useful when the directory has already been indexed and is large.
  It searches indexed chunk text, so repeated searches avoid reading every file
  from disk.
- Native shell `grep` is still excellent for small folders, one-off checks, and
  files that have not been indexed.

## Browse Commands

Browse commands do not require semantic indexing. They read the live file
system and are useful after search has found candidate paths.

### Density Presets

`cat`, `ls`, and `tree` share the same context-budget model.

| Preset | Purpose |
| --- | --- |
| `--peek` | One-glance structure. Shows names, headings, or skeletons with little or no body text. |
| `--skim` | Default overview. Shows compact summaries and short excerpts. |
| `--deep` | Richer inspection. Shows more lines, headings, and nested structure. |

Overrides:

| Option | Meaning |
| --- | --- |
| `-W INTEGER` | Width budget. Controls characters per paragraph, value, or node. |
| `-H INTEGER` | Height budget. Controls max headings, items, or entries shown. |
| `-D INTEGER` | Depth budget. Controls heading or structure levels to expand. |

### `mfs cat`

Read exact content or show a density-controlled file overview.

```bash
mfs cat FILE [OPTIONS]
```

Inputs:

| Input | Meaning |
| --- | --- |
| `FILE` | File path to inspect. Markdown, code, text, JSON, JSONL, CSV, PDF, and DOCX are handled by the browse layer. |

Options:

| Option | Meaning |
| --- | --- |
| `--peek`, `--skim`, `--deep` | Density preset. |
| `-W INTEGER`, `-H INTEGER`, `-D INTEGER` | Override density budgets. |
| `-n TEXT` | Exact line range, such as `40:60`, `40:`, or `:60`. |
| `--no-frontmatter` | Strip YAML/TOML frontmatter before display. |
| `--meta` | Force `::mfs:` metadata headers even in a terminal. |
| `--no-meta` | Omit `::mfs:` metadata headers even when piped. |
| `--json` | Emit structured output. |
| `--no-line-numbers` | Omit source line numbers in `peek`, `skim`, and `deep` views. |

Overview example:

```bash
mfs cat --skim ./README.md
```

```text
::mfs:source=/repo/demo/README.md
::mfs:indexed=true
::mfs:hash=595b1df9

  1  # Demo Project
  3    This project documents token rotation, Cedar Sweep revocation, and search examp…
  5    ## Operations
  7      Refresh tokens rotate every seven days. The background cleanup job is called Ce…
```

Exact range example:

```bash
mfs cat -n 1:4 ./README.md
```

```text
::mfs:source=/repo/demo/README.md
::mfs:indexed=true
::mfs:hash=595b1df9
::mfs:lines=1:4

# Demo Project

This project documents token rotation, Cedar Sweep revocation, and search examples.
```

JSON and JSONL:

- `mfs cat --peek data.json` shows a compact tree-like structure.
- `mfs cat --skim data.jsonl` shows representative rows and keys.
- `-D` increases structural depth for nested JSON values.

### `mfs ls`

List one directory with compact file and subdirectory summaries.

```bash
mfs ls [PATH] [OPTIONS]
```

Inputs:

| Input | Meaning |
| --- | --- |
| `PATH` | Directory to list. Defaults to the current directory. |

Options:

| Option | Meaning |
| --- | --- |
| `--peek`, `--skim`, `--deep` | Density preset. Default: `--skim`. |
| `-W INTEGER`, `-H INTEGER`, `-D INTEGER` | Override density budgets. |
| `--json` | Emit directory entries as JSON. |

Example:

```bash
mfs ls --skim ./demo
```

```text
/repo/demo/
README.md  # Demo Project  [indexed]
             This project documents token rotation, Cedar Sweep revocation, and search examp…
             ## Operations
docs/      - runbook.md: # Runbook   Use Cedar Sweep when a refresh token must be revoked across all devices.
src/       - auth.py: def rotate_token(user_id: str) -> str:
```

Output notes:

- `[indexed]` means the file has body chunks in the index.
- Stale generated summaries are marked when known.
- Binary and ignored files are listed without fabricated summaries.

### `mfs tree`

Show a recursive tree with optional per-node summaries.

```bash
mfs tree [PATH] [OPTIONS]
```

Inputs:

| Input | Meaning |
| --- | --- |
| `PATH` | Directory to traverse. Defaults to the current directory. |

Options:

| Option | Meaning |
| --- | --- |
| `--peek`, `--skim`, `--deep` | Density preset. Default: `--skim`. |
| `-W INTEGER`, `-H INTEGER`, `-D INTEGER` | Override density budgets. |
| `-L INTEGER` | Maximum directory recursion depth. Default: `3`. |
| `--json` | Emit nested tree JSON. |

Example:

```bash
mfs tree --skim -L 2 ./demo
```

```text
/repo/demo/
├── README.md  — # Demo Project
├── docs/
│   └── runbook.md  — # Runbook
└── src/
    └── auth.py  — def rotate_token(user_id: str) -> str:
```

## Configuration Commands

MFS reads configuration from `~/.mfs/config.toml` by default. Set `MFS_HOME` to
use a different config and data directory:

```bash
MFS_HOME=/tmp/mfs-demo mfs config path
```

### `mfs config path`

Print the absolute config path.

```bash
mfs config path
```

Output:

```text
/home/user/.mfs/config.toml
```

### `mfs config init`

Write a commented default config file.

```bash
mfs config init [--force]
```

Options:

| Option | Meaning |
| --- | --- |
| `--force` | Overwrite an existing config after writing a timestamped backup. |

Output:

```text
wrote /home/user/.mfs/config.toml
```

### `mfs config show`

Display all effective config values and their source.

```bash
mfs config show [--json]
```

Options:

| Option | Meaning |
| --- | --- |
| `--json` | Emit effective config as JSON. Secret values are redacted as `<set>`. |

Text output:

```text
[embedding]
  provider               = "openai"                                 [default]
  model                  = "text-embedding-3-small"                 [default]
  api_key                = <set>                                    [env OPENAI_API_KEY]
```

JSON output:

```json
{
  "embedding": {
    "provider": "openai",
    "model": "text-embedding-3-small",
    "api_key": "<set>"
  }
}
```

### `mfs config get`

Print one effective config value.

```bash
mfs config get KEY
```

Examples:

```bash
mfs config get embedding.provider
mfs config get milvus.uri
```

List values are printed as comma-separated strings.

### `mfs config set`

Persist one value to `config.toml`.

```bash
mfs config set KEY VALUE
```

Examples:

```bash
mfs config set embedding.provider onnx
mfs config set embedding.batch_size 64
mfs config set indexing.include_extensions '["md","py","txt"]'
```

Output:

```text
set embedding.batch_size = 64 in /home/user/.mfs/config.toml
```

When changing `embedding.provider` or `llm.provider`, MFS may also update the
corresponding model if the current model still matches a known provider default.

Common keys:

| Key | Example |
| --- | --- |
| `embedding.provider` | `openai`, `onnx`, `local`, `jina`, `voyage`, `google`, `mistral`, `ollama` |
| `embedding.model` | `text-embedding-3-small` |
| `embedding.batch_size` | `32` |
| `llm.provider` | `openai`, `anthropic`, `google`, `ollama`, `mistral` |
| `llm.model` | `gpt-4o-mini` |
| `milvus.uri` | `~/.mfs/milvus.db`, `http://localhost:19530`, `https://xxx.zillizcloud.com` |
| `milvus.collection_name` | `mfs_chunks` |
| `milvus.token` | Zilliz Cloud or Milvus auth token |
| `indexing.include_extensions` | `["md","py"]` |
| `indexing.exclude_extensions` | `["log"]` |
| `cache.max_size_mb` | `500` |
