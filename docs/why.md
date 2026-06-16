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

## What makes MFS different

Five things, and the point is having all of them at once:

- **🗂️ One file-like interface over any source.** Whatever the source or format —
  a repo, a Postgres table, a Slack channel, a PDF — it becomes a single
  file-like tree under a stable URI. Agents already speak shell, so there's no new
  query language and no per-source SDK; the same handful of verbs reach
  everything, and what you learn once carries across every connector.
- **🌐 Your whole workspace, not a harness per scenario.** Memory, code, docs,
  chat, tickets, databases — instead of wiring a separate retrieval setup for
  each, MFS unifies your entire working context, with its history and state,
  behind one interface. A single setup covers everything your agent works with.
- **🔍 Search and browse, two legs of one loop.** Hybrid semantic + keyword search
  locates fast across huge volumes; progressive browse then narrows to the exact
  bytes or rows. Together they lift precise recall *and* cut token spend — you
  pull in only what matters, and never trust a hit until you've reopened it. This
  loop is what the [mental model](#the-mental-model-candidates-then-evidence)
  below is all about.
- **🛡️ Local and production, equally at home.** Run fully local and offline, or at
  production scale — neither is an afterthought. Every component is swappable and
  independently scalable, so the same MFS moves between the two by configuration
  alone. The index is derived and crash-safe: upstream stays the source of truth,
  so you can delete it and rebuild from the originals, losing nothing. See the
  [Design philosophy](production.md) for the engineering behind this.
- **🤖 Agent-native.** Built for how agents actually work — especially context and
  memory management — so it slots into any agent setup. And when you're building
  an agent of your own, you can build it on top of MFS too.

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

## How MFS compares

Plenty of tools do one of these things well — code search, agent memory, managed
RAG, virtual filesystems. MFS's bet is having them all at once: broad sources,
hybrid search *and* file-like browse, an agent-native interface, self-hosted, and
engineered for production. The table sets it against the closest tools in the
space.

| Project | Many sources | Hybrid search | File-like browse | Production-grade server |
|---|:--:|:--:|:--:|:--:|
| **MFS** | Yes | Yes | Yes | Yes |
| Claude Context | — | Yes | — | — |
| memsearch | Partial | Yes | Partial | Partial |
| OpenViking | Yes | Partial | — | Partial |
| Mirage | Yes | — | Yes | — |
| CocoIndex | Yes | — | — | Yes |
| LlamaCloud | Yes | Yes | — | Yes |

*Yes · Partial · — (not a focus of that tool).* Only MFS fills every column — and
it's the only one that pairs hybrid search *with* file-like browse. See the
[Design philosophy](production.md) for the engineering behind the last column.
