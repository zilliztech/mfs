# Design philosophy

This page is the *why* behind the [Architecture](architecture.md): the design
decisions behind MFS and what each one buys you. They're the choices everything
else follows from — including the daily-use guarantees over in
[Robustness](robustness.md).

## One source of truth

Your upstream sources are the truth. MFS's metadata database is only its
*knowledge* of them; the Milvus index and the caches are **derived**. Delete the
index and rebuild it from the original sources and you lose nothing.

Clients hold almost no state — even the file manifest for an upload lives in the
server's `file_state` table, not in a client-side snapshot. There's no local
snapshot file that can drift out of sync with what's actually indexed, which is a
whole class of "the snapshot says 0 files, reindex forever" bugs that simply
can't be expressed here.

This pairs with a second rule held everywhere: **every operation is idempotent**,
so doing it twice is harmless. Those two ideas — derived-from-truth and
idempotent — are what make recovery, deletion, and re-syncing as simple as they
are; [Robustness](robustness.md) is the catalogue of what they buy.

## Agent-first, one protocol

MFS's first user is an agent, not a person. The primary interface is a
shell-native CLI built from verbs an agent already knows (`ls`, `cat`, `grep`,
`tree`, …) rather than a new query language, and it ships as a skill so an agent
arrives with the right mental model instead of trial and error.

The CLI, the SDKs, and the skills are all clients of the same HTTP `/v1` — none
has a privileged path. That keeps the three entry points behaving identically and
means adding an SDK never changes how anything else works; the SDKs are the
fallback for programs that can't comfortably shell out, not a second API.

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
