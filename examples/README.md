# Examples

Each example here rebuilds a well-known project on top of MFS — the same
capability, in a fraction of the code — because MFS already owns the part
that used to be the hard part for each of them: ingestion, chunking,
embedding, the vector store, and hybrid search across many source types.
What's left is thin glue, or a single skill describing a strategy on top of
`mfs search` / `mfs cat`.

| Example | Replicates | What it is |
|---|---|---|
| [`claude-context/`](claude-context/) | [claude-context](https://github.com/zilliztech/claude-context) — "make the codebase the context for any coding agent" | An MCP server: `search` + `read` over MFS, about 60 lines |
| [`deep-research-skill/`](deep-research-skill/) | [deep-searcher](https://github.com/zilliztech/deep-searcher) — reason and search over private data | A skill (`deep-research`) that runs the decompose → search → evaluate → synthesize loop on top of `mfs-find`; no framework, no vector-DB glue |
| [`open-tag-skill/`](open-tag-skill/) | [Claude Tag](https://www.anthropic.com/news/introducing-claude-tag) — Anthropic's hosted `@Claude` Slack teammate | A self-hosted Slack bot (`@OpenClaude` / `@OpenCodex`) plus the `open-tag-admin` skill that sets it up and runs it |

Every one of the replicated projects had to build its own retrieval stack
from scratch — a provider matrix, a vector database, document loaders, sync
logic. MFS already is that stack, so the example is only ever what's left
on top of it: an MCP server thin enough to read in one sitting, a skill
that's a strategy prompt over MFS's search API, or a small bridge process.

Open each example's own README for the install command and a full
walkthrough.
