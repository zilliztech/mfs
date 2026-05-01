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

Synchronization exists because the index is derived state. If a user edits a
file, deletes a note, appends a transcript, or converts a PDF/DOCX into cached
Markdown, the Milvus rows must eventually match the real files again.

The tempting design is to hide all of that behind every query: run
`mfs search`, silently scan the folder, detect changes, rebuild chunks, update
embeddings, and then return results. MFS avoids that for three reasons:

- **Query commands should stay read-only and predictable.** A search should not
  unexpectedly rewrite local state, start a worker, or spend seconds hashing a
  large tree.
- **Embedding is the slow part.** Scanning is usually cheap; chunking and
  embedding many changed files is not. Hiding that cost inside `search` makes
  agent behavior harder to reason about.
- **Agents need explicit control.** In some workflows stale results are fine for
  a moment; in others, the agent should force a rebuild before answering. The
  command surface should make that choice visible.

So MFS makes synchronization explicit. The user or agent updates the index when
they want the indexed view to catch up, or starts a watch loop when the workflow
really needs continuous updates:

```bash
mfs add .
mfs add . --force
mfs add . --watch --interval 60s
```

The sync path has three jobs:

```mermaid
flowchart TB
  files[Real files<br/>source of truth] --> scan[Scan with ignore rules<br/>and file type policy]
  scan --> diff[Diff against indexed<br/>source + file_hash]
  diff --> remove[Delete rows<br/>for removed files]
  diff --> changed[New or changed files]
  changed --> chunks[Chunk changed content]
  chunks --> queue[Queue lightweight chunk refs]
  queue --> worker[Worker embeds and upserts]
  worker --> dirs[Refresh affected<br/>directory summaries]
  dirs --> usable[Progressively usable index]
```

The important part is that MFS compares two views:

```text
left:  current disk state
       all files that exist now, after ignore rules and size limits

right: indexed state in Milvus
       source path, file_hash, chunk metadata, summary rows

diff:
       added    -> chunk and embed
       modified -> rechunk, compare chunk hashes, embed changed chunks
       deleted  -> remove old rows from Milvus
       unchanged -> skip
```

`mtime` is used only as a fast hint to reduce hashing work. File hash is the
content check. `--force` skips the mtime shortcut when a copy, checkout, or sync
tool may have preserved old timestamps.

The queue exists because embedding work should not block every default `mfs add`
call. It is intentionally much smaller than a broker system. MFS is a CLI tool,
so it avoids Redis, RabbitMQ, and a permanent daemon:

- queue: `~/.mfs/queue.json`
- lock: filelock around queue writes
- worker: detached Python process
- progress: `~/.mfs/status.json`
- logs: `~/.mfs/worker.log`

Queue entries store references and metadata, not large raw chunk bodies. That
keeps the queue cheap to rewrite and avoids duplicating the user's corpus in
`~/.mfs/`. When the worker needs text, it reconstructs the chunk from the
current file and the queued range/hash metadata. If the file changed while work
was waiting, the next `mfs add` reconciles the derived index with the new file
state.

The worker exits after the queue is empty. If a machine stops mid-index, MFS
does not try to provide database-grade job durability; the recovery path is to
rerun `mfs add` or use `mfs add --force`. That tradeoff keeps the system easy to
install and easy to delete: the authoritative data is still the user's files.

For large corpora, the index should become useful before every file is done.
MFS therefore prioritizes likely high-value files first: entry files like
`README.md` and `SKILL.md`, package metadata, source roots, documentation roots,
recently changed files, and then lower-value generated or fixture paths. The
final index is the same; the early search experience is better.

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
