# Design philosophy

This page is the *why* behind the [Architecture](architecture.md): the choices
that make MFS what it is. They're the ideas everything else follows from —
including the daily-use guarantees catalogued in [Robustness](robustness.md).

## One file-like interface over everything

Every source — a repo, a Postgres table, a Slack channel, a PDF — becomes a
file-like tree under a stable URI, driven by the POSIX verbs an agent already
knows: `ls`, `cat`, `grep`, `tree`, `head`, `tail`. No new query language, no
per-source SDK. You learn the handful of verbs once and they reach everything.

That's possible because **everything is a connector**: `postgres`, `slack`,
`github`, and `file` all implement the same contract and flow through the same
pipeline, so they all get the same search and the same commands. The only real
exception is `file` — its bytes live on the client rather than somewhere the
server can reach — and even that is [isolated to one upload step](architecture.md#file-is-the-special-case).
The payoff is one mental model instead of a dozen integrations.

## Search and browse, two complementary routes

Most tools pick one side: a RAG service only searches; a virtual filesystem only
browses. MFS ships **both**, because finding something across a large, mixed body
of context needs both:

- **Search** — flat, global, hybrid (semantic *and* keyword) — locates fast
  across huge volumes. It tells you *where to look*.
- **Browse** — `ls`, `tree`, `cat`, `head`, `tail` — narrows from a hit down to
  the exact bytes. It gives you something you can actually trust.

It's the same shape you already use on a search engine: a pre-built index makes a
flat query fast, the snippets help you judge which result is worth opening, then
you open the page and read. Search gives *candidates, not answers* — so you
reopen the source before quoting it. The two cover for each other: once a corpus
is large you can't browse your way to the answer (you need the index), and an
index alone is never proof (you need the browse). See
[Why MFS](why.md#the-mental-model-candidates-then-evidence) for the full loop.

## Thin client, heavy server

All the weight sits on the server — pulling data, converting, chunking,
embedding, indexing, storing. The client only parses a command, transports it
over `/v1`, and renders the result. The reasons are practical:

- **Agents call the CLI in a hot loop**, so it has to cold-start in milliseconds
  and carry no state — a small Rust binary, not a runtime with dependencies to
  load each call.
- **State in one place is what makes consistency possible.** Registration,
  indexing, change detection, and recovery are all stateful; keeping them on one
  server beats scattering them across every client.

So the client is disposable: a laptop, a CI runner, or a fresh container
reconnects with nothing to restore — the only client-side file is `client.toml`
(which server to talk to).

## Agent-first, one protocol

MFS's first user is an agent, not a person. The primary interface is the
shell-native CLI above, and it ships as a skill so an agent arrives with the
right mental model instead of trial and error.

The CLI, the SDKs, and the skills are all clients of the same HTTP `/v1` — none
has a privileged path. That keeps the three entry points behaving identically and
means adding an SDK never changes how anything else works; the SDKs are the
fallback for programs that can't comfortably shell out, not a second API.

## One source of truth

Your upstream sources are the truth. MFS's metadata database is only its
*knowledge* of them; the Milvus index and the caches are **derived**. Delete the
index and rebuild it from the original sources and you lose nothing.

Clients hold almost no state — even the file manifest for an upload lives in the
server's `file_state` table, not in a client-side snapshot. There's no local
snapshot that can drift out of sync with what's actually indexed, which is a whole
class of "the snapshot says 0 files, reindex forever" bugs that simply can't be
expressed here.

This pairs with a second rule held everywhere: **every operation is idempotent**,
so doing it twice is harmless. Those two ideas — derived-from-truth and
idempotent — are what make recovery, deletion, and re-syncing as simple as they
are; [Robustness](robustness.md) is the catalogue of what they buy.

## Built for community

The architecture's central tension is *unify the common parts, isolate the
differences* — and the difference is kept deliberately thin. Everything hard is
the framework's job: chunking, embedding, summaries, VLM, the artifact and
transformation caches, the Milvus schema, retrieval, the HTTP API, the job queue,
fingerprinting, and deletion logic. A contributor writes one connector plugin —
six required methods (`stat` / `list` / `read` / `fingerprint` / `sync` /
`object_kind_of`), a few hundred lines — that just connects the source, lays out
its URI tree, and reports changes. In return it gets the whole chunk → embed →
search → cache → store pipeline for free.

That line is drawn on purpose: a new data source is a plugin, not a fork of the
framework. The aim is for the connector catalog to grow the way Airbyte's or
Singer's did — through the community.
