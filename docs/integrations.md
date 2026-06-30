# Integrations

MFS is a retrieval layer, not a destination. This section is the ecosystem hub:
**apps built on top of MFS**, and **adapters that plug MFS into the AI frameworks
you already use**.

There are two kinds of pages here:

- **Apps on MFS** — runnable reference apps that use MFS as their searchable
  Memory.
    - [Open Tag](integrations/open-tag.md) — a Claude Tag-style Slack tag-in bot.
- **Frameworks** — drop MFS into an existing agent / RAG stack.
    - [LangChain](integrations/langchain.md) — MFS as a `Retriever` and a tool.
    - [LangGraph](integrations/langgraph.md) — MFS as a retrieval node in a graph.

## Why integrations are thin

Everything MFS exposes is a small, stable surface, so an adapter is usually a
dozen lines, not a package:

- **One HTTP control plane** (`/v1`) and two generated clients — see
  [SDKs](sdks.md) and the [HTTP API](api.md).
- **One search call** over every connected source at once: hybrid (dense + BM25)
  ranking, returned as a flat list of hits.
- **A uniform hit shape** — each result carries `source`, a `content` snippet, a
  `score`, and a `locator` that reopens the exact unit (a line range for
  code/docs, a primary-key dict for a row / issue / chat thread).

So a framework adapter is just a mapping: a search hit becomes whatever that
framework calls a retrieved chunk (a LangChain `Document`, a tool result, …),
and the `locator` lets you fetch the full unit with `cat` when a snippet is not
enough.

## What you get over a single vector store

The usual setup wires one vector store over one folder, and re-indexes per app.
MFS inverts that: it owns ingest and indexing across **all** your sources —
code, docs, Slack, Postgres, Jira, S3, … — behind one query. An adapter gives
the framework a **single retriever over everything**, self-hosted, with no
per-app re-indexing. Add a source once in MFS and every integration sees it.
