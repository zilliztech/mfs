# Why MFS

MFS is not trying to replace your shell. If you already know the exact file and
the exact string you're after, `grep`, `rg`, `find`, and `cat` are faster and
need no server and no index. Reach for those first.

MFS earns its keep when the hard part is *finding* — when the right evidence is
somewhere across a large or mixed body of context, the wording in your head
doesn't match the wording on disk, and the source you need might be a code file,
a database row, a Slack thread, or a year-old design doc. That's the moment a
plain shell runs out of road and a single, file-like search surface over every
source starts to pay off.

## When it's the right tool

Use MFS when:

- You have a conceptual question and the exact phrasing is uncertain. Hybrid
  search matches on meaning *and* keywords, so you find the answer even when it's
  worded differently than your query.
- You need to look across several kinds of source through one command surface —
  repos, object stores, databases, SaaS tools — without learning each one's API.
- You're building an agent workflow that needs structured, machine-readable
  results: every command can emit JSON carrying the `source`, `locator`, and
  metadata an agent needs to reopen a hit precisely.
- You'd otherwise stand up a separate retrieval setup for each kind of source.
  MFS is one workspace over all of them, so a single setup covers your whole
  working context — its history and state included — instead of a harness per
  scenario.

Reach for something else when:

- You want a real filesystem — POSIX mounts, writes, locks, kernel semantics.
  MFS adds search, browse, and read surfaces *over* sources; it is not a mounted
  filesystem.
- You need a vector database for your own application. MFS uses Milvus for its
  own index, but it isn't a drop-in vector store for your app — talk to Milvus or
  Zilliz directly for that.

## The mental model: candidates, then evidence

The one idea that makes MFS reliable is that **search gives candidates, not
answers**. A ranked snippet tells you where to look; it is not yet something you
should quote, summarize, edit, or decide on.

This is just how people already search. On Google you don't scroll the whole web
— you type a query and a pre-built index returns a ranked page of candidates
almost instantly. Flat, global search is fast *precisely because* the index was
built ahead of time. Then you skim the result snippets to judge which one is
worth opening, click into the page, and read and navigate it directly. MFS gives
an agent the same two moves over your own sources: fast global semantic search to
locate, then progressive browse to read.

Both halves are essential and they cover for each other. Progressive,
agent-driven browsing is powerful for working through a tree you can navigate —
but once a knowledge base or memory store gets large, you can't browse your way
to the answer; you need a semantic index to jump straight to the candidates. And
an index alone isn't enough either: a ranked snippet isn't proof. So MFS packages
both and you never have to choose — hybrid semantic search (built on Milvus) to
find, file-like browse to verify.

So the loop always has two beats:

1. **Locate.** `mfs search` (meaning + keywords) or `mfs grep` (exact literal)
   surfaces likely hits. With `--json` you get the `source` URI and `locator`
   back, which is exactly what you feed to the next step.
2. **Verify.** Reopen the real source with `mfs cat` — `--range A:B` for a span
   of lines, `--locator JSON` for a structured record, `head`/`tail` for the ends
   of something large, or `export` to pull a whole object out of the prompt.

If a result looks weak or incomplete, that's usually a clue about *indexing*, not
relevance. `mfs ls`, `mfs grep`, and the browse commands let you tell a ranking
problem apart from a "not indexed yet" problem instead of guessing.

!!! warning "Treat search results as candidates"
    Before you quote, summarize, edit, or make a decision, reopen the exact
    evidence with `cat`, `head`, `tail`, `export`, `cat --range`, or
    `cat --locator`. The snippet is where you start, not what you cite.

## Agents and humans drive it the same way

The loop is identical whether a person or an agent is at the keyboard; only the
ergonomics differ.

An **agent** should scope its search to a path when it can, keep the `source` and
`locator` from the JSON result, and reopen exact evidence before editing or
answering — never act on a ranked snippet alone or guess at a locator shape.

A **person** leans on the human-readable output: `mfs tree` to get oriented,
`mfs search` to find a starting point, and `mfs cat --range` to read just enough
context. Save `--all` for when you genuinely don't know which source holds the
answer; a scoped path is faster and easier to trust.

## How MFS compares

Plenty of tools do one of these things well. MFS's bet is the *combination* —
broad sources, both search and browse, an agent-native interface, self-hosted,
and engineered to run in production. Compared to the usual alternatives:

| | MFS | Code-search tools | Memory layers | Managed RAG services |
|---|:--:|:--:|:--:|:--:|
| Many source types — files, DBs, SaaS, chat | ✅ | ❌ | ❌ | ➖ |
| Hybrid semantic + keyword search | ✅ | ➖ | ➖ | ✅ |
| File-like progressive browse (`ls` / `cat` / `--range`) | ✅ | ❌ | ❌ | ❌ |
| Agent-native CLI + skills | ✅ | ➖ | ➖ | ❌ |
| Self-hosted and open source | ✅ | ➖ | ➖ | ❌ |
| Stateful server — concurrency-safe, resumable, idempotent | ✅ | ❌ | ❌ | — |

✅ built in · ➖ partial or varies · ❌ not the focus. The point isn't any single
row — it's having every row at once. See [Built for production](production.md)
for the engineering behind the last one.

## Where to next

If this sounds like your problem, the [Quickstart](getting-started.md) gets you
running in a few minutes. To understand the pieces first, read
[How it works](architecture.md). To bring your own sources in, start with
[Connectors](connectors.md).
