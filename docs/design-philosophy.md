# Design Philosophy

MFS is built as infrastructure for agents: a thin file-search layer over
ordinary workspaces, not a replacement filesystem and not a closed knowledge
product.

The design can be reduced to five principles.

## 1. Files Stay the Source of Truth

The user's files are the durable state. Milvus stores a derived index.

That keeps the system predictable:

- deleting `~/.mfs/` does not delete user knowledge
- `mfs add .` can rebuild the index from the actual files
- file deletion should remove derived index records
- Git, editors, `grep`, `cat`, and `find` continue to work normally
- project folders do not receive generated sidecar files

MFS state lives under `~/.mfs/` by default: config, queue, Milvus Lite database,
worker status, logs, and converted PDF/DOCX cache. The indexed project
directory stays clean.

## 2. Search and Browse Are Both Necessary

MFS gives agents two complementary ways to get information:

- **flat global search** with `mfs search` and `mfs grep`
- **progressive local browsing** with `mfs ls`, `mfs tree`, and `mfs cat`

They solve different jobs. Search answers **where might the answer be?** Browse
answers **what is around this result, and what should I read next?**

This is close to how people use the web: first use a prebuilt index to find
candidate pages, then read previews, then open the specific page and navigate
inside it. The index matters because at large scale a blind browse is too slow.
The browse step matters because one search result rarely contains all the local
context an agent needs.

It is also close to how people use a library: search the catalog first, inspect
the right shelf next, then read the exact pages.

MFS keeps both paths available:

```bash
mfs search "how does token revocation work" ./docs --mode hybrid
mfs grep "ERR_TOKEN_EXPIRED" ./docs
mfs tree --peek -L 2 ./docs
mfs cat --skim ./docs/auth.md
mfs cat -n 80:140 ./docs/auth.md
```

MFS searches body chunks directly, not only summaries. This preserves exact
anchors such as error codes, function names, config keys, feature flags, table
labels, and transcript phrases. Optional generated summaries add another
retrieval surface, but they do not replace the original chunks.

## 3. Browsing Needs a "Look Once" Layer

Traditional shell tools leave a gap:

- `cat` is too detailed and can waste tokens on whole files
- `ls` and `tree` show names but too little content
- `grep` shows local matches but not the overall shape

Agents need a fast way to look at a file or directory and understand its rough
structure before deciding where to spend context.

Most useful local data already has a tree shape:

| Data | Natural structure |
| --- | --- |
| Markdown | headings and paragraphs |
| Code | modules, classes, functions, symbols |
| JSON | nested keys and values |
| JSONL | rows, then keys inside each row |
| CSV | headers, rows, cells |
| Directory | child files and subdirectories |

To "look once", MFS renders the trunk before the leaves. It exposes the same
information-density controls across `cat`, `ls`, and `tree`:

| Control | Meaning |
| --- | --- |
| `-W` | width: characters per node, value, paragraph, or summary |
| `-H` | height: number of headings, rows, entries, or children |
| `-D` | depth: nested levels to expand |

The presets are shortcuts:

- `--peek`: structure only
- `--skim`: compact overview
- `--deep`: richer context

Markdown is a good example. It already carries semantic structure in headings
and first paragraphs, so MFS can show a useful outline without asking an LLM to
summarize the whole file.

## 4. Sync and Queueing Stay Lightweight

MFS does not silently rescan on every query. The user or agent updates the index
explicitly:

```bash
mfs add .
mfs add . --force
mfs add . --watch --interval 60s
```

The sync model is simple:

```text
scan disk files
  -> compare with indexed {source, file_hash}
  -> delete rows for removed files
  -> queue new or changed chunk refs
  -> embed queued work
  -> rebuild affected directory summaries
```

`mtime` is used as a fast hint, and file hash is the content check. `--force`
skips the mtime shortcut when a copy, checkout, or sync tool may have preserved
old timestamps.

The queue is also intentionally small. MFS avoids Redis, RabbitMQ, and
long-running services:

- queue: `~/.mfs/queue.json`
- lock: filelock around queue writes
- worker: detached Python process
- progress: `~/.mfs/status.json`
- logs: `~/.mfs/worker.log`

Queue entries store references and metadata, not large raw chunk bodies. The
worker exits after the queue is empty. If a machine stops mid-index, the
recovery path is to rerun `mfs add` or use `mfs add --force`.

For large corpora, MFS prioritizes likely high-value files first: entry files
like `README.md` and `SKILL.md`, package metadata, source roots, documentation
roots, and then lower-value generated or fixture paths. The final index is the
same; early usefulness is better.

## 5. Everything Should Become Searchable

MFS starts with the file types agents use today: Markdown, source code, text,
PDF, DOCX, JSON, JSONL, CSV, directories, and images with generated
descriptions.

Different formats enter the system in different ways:

- Markdown, code, text, PDF, and DOCX can be embedded as body chunks.
- PDF and DOCX are converted to Markdown first and cached.
- JSON, JSONL, CSV, YAML, TOML, HTML, and logs are readable through structured
  browse views and searchable with `mfs grep`.
- Images can become searchable through `mfs add --describe`, which stores a VLM
  text description in the same collection.

The long-term direction is broader: every useful local artifact should have a
searchable representation, including more multimodal formats. The principle is
the same whether the source is text, a transcript, a table, a PDF, an image, or
future media types: keep the original file as truth, derive the searchable
surface, and let the agent search broadly before browsing precisely.
