# Local files (`file`)

The `file` connector indexes a local directory tree — a code repo, a docs
folder, a dump of PDFs. It's the connector you'll reach for first, and the only
one with no credentials and no optional dependencies: it's always available.

You point it at a directory and every file underneath becomes a searchable,
browsable object at a stable URI. Re-run `mfs add` to re-sync after the files
change.

## How MFS sees it

Every file keeps its real name and extension under the connector root. On the
same host the server identity for a path is `file://local/abs/path`; an uploaded
tree (see [upload mode](../getting-started.md#7-use-upload-mode-for-true-clientserver-runs))
is keyed by the client id instead: `file://<client_id><abs-root>/...`.

```text
file://local/home/alice/project/
├── README.md            document  → converted, embedded, searchable
├── src/
│   └── engine.py        code      → embedded, searchable
├── docs/spec.pdf        document  → converted, embedded, searchable
├── data/users.csv       text_blob → browse + grep, NOT semantically searchable
└── build/app.bin        binary    → browse / export only
```

## What gets indexed

The connector classifies each file by extension, and the class decides whether it
becomes part of semantic search:

| Class | Extensions | In semantic search? | You can still… |
|---|---|---|---|
| `code` | `.py .js .ts .tsx .go .rs .java .c .cpp .rb .php .sql .sh` … | Yes — embedded as code chunks | `cat`, `grep` |
| `document` | `.md .txt .rst .pdf .docx .pptx .xlsx .html` | Yes — converted to text, then embedded | `cat`, `grep` |
| `image` | `.png .jpg .gif .webp .svg` … | Only when VLM descriptions are enabled | `export` |
| `text_blob` | `.json .csv .tsv .yaml .toml .ini .log .jsonl .ndjson` | **No** — not embedded | `cat`, `grep` |
| `binary` | everything else | No | `cat` (raw), `export` |

The distinction that surprises people: structured text like `.json`, `.csv`, and
`.yaml` is **browseable and greppable but not in semantic search**. `mfs search`
won't rank a CSV row; `mfs grep` and `mfs cat` will still find and read it. If you
want a CSV to be semantically searchable, ingest it through a database connector
instead, where rows become first-class records.

## Configuration

The simple case needs no TOML at all:

```bash
mfs add ./docs
```

Optional TOML tunes the edges — cap file size, or mark a subtree browse-only:

```toml
max_file_bytes = 5_000_000

[[objects]]
match = "/data/exports"
indexable = false        # stays browseable, never embedded
```

`indexable = false` keeps an object in the tree for `ls`/`cat` but skips the
embedding step.

## What's left out of the tree

The connector honors ignore rules so you don't index noise:

- Built-in defaults drop `node_modules/`, `.git/`, `__pycache__/`, `*.pyc`, and
  similar generated paths.
- A `.gitignore` or `.mfsignore` at the root extends the ignore set.
- Files over `max_file_bytes` are skipped.

Symlinks are resolved inside the connector root; any path that escapes the root
(`../secret`) is rejected outright.

## Sync and freshness

Re-running `mfs add <path>` re-syncs. The connector has no remote cursor — it does
a **full scan** and diffs against indexed state, so files added, changed, *and*
deleted are all reflected on the next sync (`delete_detection = full_scan`).
Unchanged files aren't re-embedded; the transformation cache already holds their
vectors. `grep` runs as a pushdown directly over the files rather than over the
index, so it works even before indexing finishes.

## Search and browse

```bash
mfs search "release checklist" ./docs --top-k 10
mfs grep "TODO" ./src
mfs cat ./docs/README.md --range 1:80
mfs tree ./src -L 2
```
