# Architecture and Mechanics

This reference explains how MFS works internally so you can choose the right
command and interpret its results correctly.

## Design Model

MFS is a shell-native retrieval layer over local files. It is not a new
filesystem and it does not take ownership of the project directory.

The source files remain the source of truth. The Milvus collection, conversion
cache, queue, status file, and config under `$MFS_HOME` are derived state. They
can be rebuilt from local files with `mfs add`, and stale index records are
removed when MFS detects deleted or changed files.

The workflow has two legs:

- Search: `mfs search` and `mfs grep` locate likely files and line ranges.
- Browse: `mfs cat`, `mfs ls`, and `mfs tree` inspect structure, excerpts, and
  exact line windows.

Use both legs when the answer needs evidence. Search is for candidate
discovery; browse is for verification.

## Indexing and Sync

`mfs add <path>` scans files, classifies file types, converts supported binary
documents to Markdown, chunks text, and sends chunk embedding work through the
same processing path used by the background worker.

By default, `mfs add` is asynchronous:

1. It computes added, modified, deleted, and unchanged files.
2. It writes chunk tasks into `$MFS_HOME/queue.json`.
3. It starts a detached worker.
4. The worker embeds queued chunks and writes records to Milvus.

Use `mfs status` to check whether background indexing is still running. Use
`mfs add <path> --sync` when the current task needs the index to be ready before
continuing; sync mode embeds in the foreground and bypasses the on-disk queue
for normal body chunks.

Change detection compares the current scan with indexed sources in Milvus:

- added: present on disk but not in the index
- deleted: present in the index but not on disk
- modified: file timestamp is newer than the last sync time and the content
  hash differs
- unchanged: already indexed and not detected as changed

`mfs add --force` skips the timestamp fast path and performs full hash
comparison.

The queue is lightweight local state, not a durable distributed job system. If
the process or machine stops during async indexing, rerun `mfs add <path>`; use
`--force` if you want a full re-check.

## Search Mechanics

`mfs search` searches indexed content and therefore needs an index. It requires
an explicit path scope or `--all`, unless it is reading a scoped MFS pipe.

Search modes:

- `hybrid`: default. Combines dense semantic search with keyword/BM25 scoring.
- `semantic`: embeds the query and ranks by vector similarity.
- `keyword`: uses keyword search only.

Use `hybrid` first for most natural-language queries. Use `semantic` when the
wording in the files may differ from the request. Use `keyword` or `mfs grep`
when exact terms should dominate.

## Grep and Non-Indexed Files

`mfs grep` is for exact strings and regex-like patterns. It requires a path
scope or `--all`, unless stdin is present.

For indexed files, MFS uses a BM25 prefilter to pick likely sources and then
reads the files to find exact line matches. For a concrete directory path, it
also scans files that are classified as readable but not embedded by default,
using system `grep` when available and a Python fallback otherwise.

This means JSON, JSONL, CSV, YAML, TOML, HTML, XML, CSS, env files, and logs are
not embedded by default, but they can still be browsed and matched with
`mfs grep <pattern> <path>`.

## Browse Mechanics

Browse commands read from the local filesystem and can work without an index:

- `mfs cat <file>` shows a known file.
- `mfs ls <dir>` lists one directory with compact file summaries when
  available.
- `mfs tree <dir>` shows a recursive directory map.

Indexing can improve summaries and status metadata, but browsing is primarily a
filesystem operation.

Use density presets to control how much context is shown:

- `--peek`: small outline or skeleton
- `--skim`: compact overview
- `--deep`: larger structured expansion

Use `-W`, `-H`, and `-D` to control excerpt width, item count, and structure
depth. Use `mfs cat -n A:B <file>` for exact line-range verification.

## Pipe Mechanics

MFS commands are designed to behave predictably in shell pipelines.

`mfs cat` can emit MFS metadata headers when its output is piped. The headers
start with `::mfs:` and can include fields such as source path, indexed status,
line count, file hash, and converted source type.

Downstream behavior:

- Headered `mfs cat` output scopes `mfs search` to the original source file.
- Plain non-empty stdin is searched as temporary chunks, not as the global
  index.
- Empty stdin returns empty results instead of silently searching an unrelated
  corpus.

This keeps pipeline behavior explicit: a pipe means "use this input" unless the
pipe carries MFS headers that identify an indexed source.

## File Type Handling

Indexed by default:

- Markdown, reStructuredText, and plain text
- common source code and script files
- PDF and DOCX after Markdown conversion

PDF conversion uses `pymupdf4llm`. DOCX conversion uses `python-docx`; headings
are converted to Markdown headings and tables are converted to Markdown tables.
Converted Markdown is cached under `$MFS_HOME/converted/<hash-prefix>/<hash>.md`.
The cache is LRU-bounded by `cache.max_size_mb` and defaults to 500 MB.

Readable but not embedded by default:

- JSON, JSONL, NDJSON, CSV, TSV
- YAML, TOML, INI, env files
- HTML, XML, CSS, Sass/Less, logs

These formats are still useful with `mfs cat`, `mfs ls`, `mfs tree`, and
`mfs grep`.

Images are ignored by normal text indexing. They become searchable only when a
text description is generated:

- `mfs add <path> --describe` with a vision-capable provider

The searchable record is the text description, not a direct image embedding.

Files larger than 10 MB are skipped by the scanner for indexing. For large
local files, prefer targeted browsing or exact search with native tools when
possible.

## State and Safety

MFS keeps project directories clean. Runtime state lives under `$MFS_HOME`,
normally `~/.mfs`:

- `config.toml`: provider and storage configuration
- `status.json`: current indexing status and sync timestamps
- `queue.json`: async embedding tasks
- `converted/`: cached Markdown converted from PDF and DOCX
- Milvus Lite data, if using local Milvus Lite

MFS can also connect to Milvus server or Zilliz Cloud, depending on
configuration. Before removing or recreating a collection, inspect existing
collections and avoid touching data that does not belong to the current MFS
configuration.
