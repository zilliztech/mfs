# Deep Research Example

[deep-searcher](https://github.com/zilliztech/deep-searcher) is Zilliz's earlier
open-source "reason and search on private data" project — decompose a question,
iteratively search a vector database, evaluate the evidence, synthesize a cited
report. It predates agentic coding tools, so it had to be a full standalone
framework: a multi-provider LLM/embedding/vector-DB/document-loader matrix, plus
custom Python orchestrating the iterative retrieval loop itself.

This example is the same job, minimal on top of MFS: MFS already handles
ingestion and hybrid search across many source types, and an agentic coding tool
already runs a "search, judge, follow up, repeat" loop natively once it has a
search tool. What used to be a whole framework is now one skill describing the
decompose → search → evaluate → synthesize **strategy** — no vector-DB glue, no
provider matrix, no orchestration code.

## Install the skill

Deep Research ships as the `deep-research` skill. It lives under `examples/`, so
install it with `--full-depth` (the default scan only covers top-level skills):

```bash
npx skills add zilliztech/mfs --full-depth --skill deep-research -a claude-code -a codex -g
```

It depends on the `mfs-find` skill for the actual search/read mechanics — install
that too if you haven't:

```bash
npx skills add zilliztech/mfs --skill mfs-find -a claude-code -a codex -g
```

## Quick start

Index whatever you want researchable (see `mfs-ingest` if nothing's indexed
yet), then just ask for something broader than a one-hit lookup:

> Write a report on how we handle rate limiting — current implementation, past
> incidents, and any open design questions. Cite sources.

The skill decomposes that into a few angles, runs `mfs search` per angle, checks
whether the coverage actually answers the question, follows up on gaps with 1-3
more targeted searches, then writes the report with inline citations back to the
MFS `source` URIs — the same shape of output `deepsearcher.query(...)` produces,
without running any of deep-searcher's own code.

## What you don't need

No vector database to stand up yourself (MFS already has one), no LLM/embedding
provider matrix to configure per-framework (MFS's own `[embedding]` config is
enough), no document loaders to wire up (MFS's connectors already cover files,
web, code, issues, chat, and structured data), and no custom retrieval-loop code
— that loop is just how an agent with a search tool already behaves once you tell
it the strategy. The skill in [`deep-research/`](deep-research/) is the whole
example: one `SKILL.md`, no scripts, no services to run.

## When to still reach for deep-searcher itself

This example covers "index my content, then run deep research against it inside
my coding agent." Reach for deep-searcher's own framework instead when you need
it as a standalone service outside an agent context — e.g. its FastAPI server for
non-agent callers, or a provider it supports that MFS's embedding registry
doesn't (see [`docs/providers.md`](../../docs/providers.md) for MFS's current
provider set).
