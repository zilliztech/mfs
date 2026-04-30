# Design Philosophy

MFS is built as infrastructure for agents, not as a replacement filesystem or a
closed knowledge product. The design favors ordinary files, explicit commands,
and predictable indexing behavior.

## Files Are the Source of Truth

The user's files are the durable state. Milvus stores a derived index.

That principle keeps the system simple:

- deleting `~/.mfs/` does not delete user knowledge
- `mfs add .` can rebuild the index from the actual files
- file deletion should remove derived index records
- Git, editors, `grep`, `cat`, and `find` continue to work normally
- project folders do not receive generated sidecar files

MFS state lives under `~/.mfs/` by default: config, queue, Milvus Lite database,
worker status, logs, and converted PDF/DOCX cache.

## Search Body Chunks, Not Just Summaries

Agents often need exact anchors: error codes, function names, config keys,
feature flags, table labels, class names, or a phrase from a transcript. These
details are easy to lose if retrieval is built only on generated summaries.

MFS indexes body chunks directly. Optional generated summaries can add another
retrieval surface, but they do not replace the original chunks.

```bash
mfs add ./docs
mfs search "ERR_TOKEN_EXPIRED" ./docs --mode keyword
mfs search "how does token revocation work" ./docs --mode hybrid
```

This lets exact and semantic retrieval work over the same source files.

## Search and Browse Have Different Jobs

Search answers: **where might the answer be?**

Browse answers: **what is around this result, and what should I read next?**

MFS keeps those jobs separate:

- `mfs search` performs flat retrieval across the indexed corpus.
- `mfs grep` handles exact search across indexed and non-indexed text.
- `mfs ls`, `mfs tree`, and `mfs cat` expose the file hierarchy and local
  structure.
- `mfs cat -n start:end` reads exact lines once the target is clear.

This separation matters for agents. A search engine should not pretend to make
all browsing decisions. An agent should not replace corpus-wide search with a
blind directory walk.

## W/H/D Controls Information Density

Agent tools need a middle granularity between `ls` and full-file `cat`.

MFS uses three density knobs across `cat`, `ls`, and `tree`:

| Knob | Meaning | Examples |
| --- | --- | --- |
| `-W` | Width: characters per node, paragraph, value, or summary | shorter or longer preview text |
| `-H` | Height: number of headings, symbols, rows, or children | fewer or more top-level items |
| `-D` | Depth: levels of nested structure to expand | headings, JSON keys, directory levels |

The presets are shortcuts over those knobs:

- `--peek`: structure only
- `--skim`: compact overview
- `--deep`: richer context

Different file types map naturally onto this model:

| File type | Structure | What browse shows |
| --- | --- | --- |
| Markdown | heading tree | headings plus bounded paragraph previews |
| Code | module, class, function, symbol structure | signatures and local excerpts |
| JSON / JSONL | nested keys and rows | compact structural preview |
| CSV | headers and sample rows | table shape and representative rows |
| Directory | child files and subdirectories | names, indexed state, summaries |

The goal is controlled context spending: enough information to decide the next
step, not enough to flood the agent.

## Markdown Already Carries Structure

Markdown notes, memory summaries, runbooks, and skill files often contain useful
semantic structure in their headings and first paragraphs. MFS uses that
structure directly for chunking and browse summaries.

Default indexing does not need an LLM to understand a Markdown outline. The
agent can use the outline through `mfs cat --peek` or `mfs cat --skim`, then
read exact lines when needed.

Generated LLM summaries are opt-in:

```bash
mfs add ./docs --summarize
```

They are useful for some vague document-retrieval workloads, but the base path
remains deterministic and cheaper to run.

## No LLM in the Default Hot Path

Default ingestion uses deterministic file scanning, conversion, chunking,
embedding, and Milvus insertion. It does not call an LLM for every file.

Optional enrichment is explicit:

```bash
mfs add ./docs --summarize
mfs add ./assets --describe
```

This keeps MFS usable in offline or restricted environments when configured with
a local embedding provider, and avoids making the index pipeline depend on
generation latency or summary cache consistency.

## Synchronization Is Explicit

MFS does not silently rescan on every query. The user or agent updates the index
with `mfs add`.

```bash
mfs add .
mfs add . --force
mfs add . --watch --interval 60s
```

The sync model is intentionally lightweight:

1. scan the requested files
2. compare current sources with indexed sources in Milvus
3. use mtime as a fast hint and file hash as the content check
4. delete records for removed files
5. queue new or changed chunks
6. rebuild affected directory summaries
7. let the worker embed queued tasks

`--force` skips the mtime shortcut and recomputes hashes. Use it after unusual
operations that preserve old timestamps, such as some `rsync` or copy flows.

## The Queue Is Lightweight, Not a Broker

Embedding can be the slow part, so the default `mfs add` path queues work and
starts a detached worker. The worker exits after the queue is empty.

MFS avoids Redis, RabbitMQ, and long-running services:

- queue: `~/.mfs/queue.json`
- lock: filelock around queue writes
- worker: detached Python process
- progress: `~/.mfs/status.json`
- logs: `~/.mfs/worker.log`

Queue entries store references and metadata, not large raw chunk bodies. If a
machine stops mid-index, the recovery path is to rerun `mfs add` or use
`mfs add --force`.

## Make Large Corpora Useful Early

When thousands of chunks are waiting, the final index is the same regardless of
order. But early usefulness matters for agents.

MFS prioritizes likely high-value files first:

- root entry files such as `README.md`, `SKILL.md`, `CLAUDE.md`, `INDEX.md`
- package and build metadata such as `pyproject.toml`, `package.json`, `go.mod`
- source roots such as `src`, `lib`, `app`, `services`
- documentation roots such as `docs`, `guides`, `reference`
- generated, vendor, build, and fixture paths later

The same priority idea also helps browse commands show important entries near
the top.

## Keep the Project Directory Clean

MFS does not write `.abstract.md`, `.overview.md`, `.mfs/`, or other generated
state into the target project. This avoids polluting repositories and avoids a
second synchronization problem where generated files themselves need to be
tracked, ignored, or updated.

The project directory stays the user's directory. MFS is the index and browse
layer around it.
