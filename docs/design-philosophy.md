# Design Philosophy

MFS is built as infrastructure for agents: a thin file-search layer over
ordinary workspaces, not a replacement filesystem and not a closed knowledge
product.

The design can be reduced to five principles.

## 1. Search and Browse Are Both Necessary

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

## 2. Browsing Needs a "Look Once" Layer

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

LLM summaries are still available as an optional enrichment path. They are not
required for normal browsing, but they can help broad, macro-level queries land
on the right file or directory.

During progressive browsing, agents should not rely only on MFS browse commands.
`mfs cat`, `mfs ls`, and `mfs tree` are designed to work alongside native Linux
tools such as `grep`, `find`, `sed`, `awk`, and shell pipelines.

## 3. Files Stay the Source of Truth

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

Because files are the source of truth, the index must follow file changes. When
files are added, edited, removed, or converted from PDF/DOCX, MFS needs to detect
what changed, update the affected chunks, recompute embeddings where needed, and
refresh directory summaries.

```text
real files change
  -> detect diff
  -> update derived index
  -> keep search and browse aligned with disk
```

## 4. Sync and Queueing Stay Lightweight

Synchronization is necessary, but it should stay small enough for a CLI tool.
MFS does not silently rescan on every query. The user or agent updates the index
explicitly, or starts a watch loop when the workflow needs it:

```bash
mfs add .
mfs add . --force
mfs add . --watch --interval 60s
```

The sync path has three concerns:

```mermaid
flowchart LR
  files[Files are source of truth] --> diff[Diff<br/>what changed?]
  diff --> embed[Embedding work<br/>what needs vectors?]
  embed --> queue[Queue<br/>how to process without blocking?]
  queue --> usable[Progressively usable index]
```

The concrete flow is:

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

The queue is intentionally small. MFS avoids Redis, RabbitMQ, and long-running
services:

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

This supports several sync styles:

| Scenario | Sync style |
| --- | --- |
| one-time project indexing | `mfs add .` |
| suspicious timestamps or external copy | `mfs add . --force` |
| active memory/log append workflow | `mfs add . --watch` |
| frequent project edits | `mfs add . --watch --interval 60s` |

## 5. Everything Should Become Searchable

AI workspaces already contain many kinds of knowledge: memory files, raw
transcripts, codebases, product documents, customer notes, PDFs, tables,
runbooks, and SKILL trees. In enterprise environments this information will only
grow. It should be searchable infrastructure, not a pile of disconnected files
that every agent has to rediscover from scratch.

MFS starts with the file types agents use today: Markdown, source code, text,
PDF, DOCX, JSON, JSONL, CSV, directories, and images with generated
descriptions. The goal is not only "search text files"; it is to make useful
workspace knowledge addressable by agents.

Different formats enter the system in different ways:

- Markdown, code, text, PDF, and DOCX can be embedded as body chunks.
- PDF and DOCX are converted to Markdown first and cached, so these awkward
  document formats can be handled directly instead of requiring manual exports.
- JSON, JSONL, CSV, YAML, TOML, HTML, and logs are readable through structured
  browse views and searchable with `mfs grep`.
- Images can become searchable through `mfs add --describe`, which stores a VLM
  text description in the same collection.

The long-term direction is broader: every useful local artifact should have a
searchable representation. Images are the current multimodal path through VLM
descriptions; future formats could include video, audio, music, screenshots, and
other media. The principle is the same whether the source is text, a transcript,
a table, a PDF, an image, or future media types: keep the original file as
truth, derive the searchable surface, and let the agent search broadly before
browsing precisely.
