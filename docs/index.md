# MFS

A modern AI agent runs on an enormous amount of **context** — and that context
is scattered. It lives in code repos, past-session memory, design docs, PDFs,
Notion pages, Slack threads, Jira tickets, Postgres tables, S3 buckets. For any
given task, the hard part is rarely reading one file you already know about. The
hard part is finding *which slice of all that context actually matters*, across
sources that each speak a different protocol.

MFS gives an agent one way to reach all of it. Every source becomes a file-like
tree under a stable URI, and the agent works across them with the same handful
of commands it already understands:

```bash
mfs add postgres://reports          # register and index a source, once
mfs search "churn assumptions" --all # find candidates across everything
mfs cat <hit> --range 40:80          # reopen the exact lines that matter
```

No per-source SDK, no bespoke retrieval glue. Register a source once; from then
on it is searchable and browsable like a local folder.

## Search and browse, two legs of one loop

MFS is built around a single loop, and both halves earn their place:

- **Search** finds candidates fast across huge, mixed volumes — hybrid semantic
  *and* keyword recall in one query. It tells you *where to look*.
- **Browse** narrows from a hit down to the exact bytes — `ls`, `tree`, `cat`,
  `head`, `tail`. It gives you something you can actually trust.

Search results are starting points, not evidence. The discipline that makes MFS
reliable is simple: search to locate, then reopen the exact source before you
quote it, edit it, or act on it. That loop lifts precise recall *and* cuts the
tokens an agent burns getting there.

## How you drive it

MFS ships as two agent **skills** — bundles of commands an agent loads and calls
on its own:

- **mfs-ingest** brings sources in: `mfs add` registers and indexes any
  connector (re-run to re-sync), and `mfs connector` lists, inspects, and removes
  them.
- **mfs-find** finds across what's ingested: `search` and `grep` to locate fast,
  then `ls`, `tree`, `cat`, `head`, `tail` to read down to the exact byte.

You can also drive the same commands by hand from a shell, or call the server
directly over HTTP and the generated SDKs when you're building something on top.

## Under the hood

A thin Rust CLI (`mfs`) talks to a stateful Python server (`mfs-server`) over an
HTTP `/v1` control plane. The server owns connectors, indexing, retrieval,
metadata, and caching, and indexes content into Milvus for hybrid search.

It runs the same either way: fully local and offline on a laptop — ONNX
embeddings, Milvus Lite, and SQLite, no cloud account required — or at
production scale with a managed Milvus/Zilliz cluster, Postgres, and a pool of
workers. Neither is an afterthought; the same components simply swap underneath.

## Start here

- **[Quickstart](getting-started.md)** — install the CLI, run the server, index
  a folder, and verify search and browse in a few minutes.
- **[Why MFS](why.md)** — when MFS earns its keep, and when a plain shell is the
  better tool.
- **[How it works](architecture.md)** — the client/server design and where each
  piece lives.
- **[Connectors](connectors.md)** — every source you can bring in, and how to
  configure each one.
