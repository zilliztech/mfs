# Design Philosophy

MFS is shaped around a few constraints that make it practical for agent work.

## Files are the source of truth

MFS never asks users to migrate their knowledge into a database. The original
files stay where they are. The index is derived state.

That means:

- deleting `~/.mfs/` should not destroy user data
- a fresh `mfs add .` can rebuild the index
- project folders are not polluted with generated summaries or state files
- normal editors, Git, `grep`, `cat`, and `find` still work

## Search should reach body chunks

MFS embeds body chunks, not only high-level summaries. Summary-only retrieval
can lose exact details: error codes, function names, configuration keys, table
labels, and other anchors that matter when an agent must act.

Optional summaries exist, but they are an additional retrieval surface, not a
replacement for body chunks.

## Browse is a first-class capability

Search gives candidates. Browse gives surrounding structure.

MFS treats `ls`, `tree`, and `cat` as retrieval commands, not afterthoughts.
They share a density model:

- `--peek` for shape only
- `--skim` for a compact overview
- `--deep` for richer context
- `-W`, `-H`, and `-D` for custom budgets

This lets an agent spend a small, controlled amount of context before deciding
where to drill in.

## No LLM in the default hot path

Default indexing uses deterministic chunking, conversion, embedding, and
Milvus insertion. It does not call an LLM to summarize every file.

LLM summaries and VLM image descriptions are opt-in:

```bash
mfs add ./docs --summarize
mfs add ./assets/diagram.png --description "Architecture diagram ..."
```

This keeps the base path cheaper, easier to reproduce, and easier to run in
restricted environments.

## Project directories stay clean

All MFS state lives under `~/.mfs/` by default:

- `config.toml`
- `milvus.db` for Milvus Lite
- `queue.json`
- worker status files
- converted PDF/DOCX Markdown cache

The indexed repo or document folder does not receive hidden metadata files.

## Explicit scope beats surprising defaults

`mfs search` and `mfs grep` require a path scope or `--all` when they are not
reading from stdin.

```bash
mfs search "auth refresh" .
mfs search "auth refresh" --all
```

This prevents a command launched from a temporary directory or pipeline from
silently searching the wrong corpus.
