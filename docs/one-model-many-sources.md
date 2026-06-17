# One model, many sources

A repository of code, a Postgres table, a Slack workspace, a folder of PDFs, an
S3 bucket — these have almost nothing in common. They differ in **shape** (a file
vs. a row vs. a message thread), in **access** (read a disk vs. call an API vs.
run a query), in **how they change** (a modified time vs. a row's timestamp vs. an
etag), and in **what they cost** to process. Left to themselves, each would demand
its own indexer, its own search stack, its own everything.

MFS's central bet is that all of that difference can be made to *disappear before
it reaches the rest of the system*. Every source is projected onto **one small
model**, and from that point on — search, browse, caching, recovery — nothing
knows or cares what the source originally was. The whole design is really one
discipline: deciding where to force that uniformity, and where to leave a source
free.

## Everything becomes a file-like tree

The first projection is the boldest. Whatever the source, it is presented as a
tree of **objects** — each either a file-like thing you can read or a
directory-like thing you can list. A database table, a Slack channel, a Drive
folder all flatten onto the same `ls` / `cat` / `tree` surface. An agent that
knows how to walk a folder already knows how to walk every source.

This works because "file-like" means *addressable and readable*, not *stored as a
file*. A row set is readable; a channel is listable. That is enough — and the
moment it holds, the entire browsing experience is written once and works
everywhere.

## A handful of shapes, not a thousand

Underneath the tree, every object is sorted into a **small, fixed, closed set of
kinds** — prose documents, code, images, tabular rows, record collections,
message threads, and a few more. A source's job is only to say which kind each of
its objects is; the kind then decides everything downstream — how the object is
split, whether and how it's embedded, what a result drawn from it looks like.

This is the keystone of the unification, and it rests on a quiet observation: the
variety of the world's data is enormous, but the variety of *what you must do to
make something searchable* is small. Pin each object to one of a few kinds and the
expensive machinery — chunking, embedding, indexing — is built once and inherited
by every source, the ones that exist today and the ones added later. Bringing in a
new source becomes a matter of *classifying* it, not of building a pipeline for
it.

## One way to point, one way to change

Two more things must be uniform for the model to hold together.

**Pointing.** A search hit has to be reopenable whether it's a span of lines in a
file, a single row of a table, or a thread in a channel. So every hit carries the
same kind of handle — a stable address for the object plus a small pointer to the
slice inside it — and the same read command reopens any of them. You never learn a
per-source addressing scheme; a result from anywhere is a result you can act on.

**Changing.** Every source detects change in its own way. MFS doesn't try to
unify the *detection* — it unifies the *report*. However a source works out what
moved, it speaks the same short vocabulary back: this appeared, this changed, this
is gone. Re-syncing, incremental updates, and the careful handling of deletions
are then written once against that vocabulary, never against one source's quirks.

## Where uniformity stops — on purpose

A model that forced *everything* the same would be a straitjacket. A database has
a change cursor a plain folder doesn't; a query can filter at the origin far more
cheaply than scanning files can. So MFS draws a second, deliberate line: each
source **declares what it can do**, and the framework plans around the
declaration. A source that can search or filter at the origin is allowed to; one
that can't is given the framework's general path instead. The differences aren't
hidden — they're stated in a common form, so the system can adapt to them
uniformly rather than special-casing each source by name.

The other reserved freedom is **layout**. Each connector designs its own tree —
what the directories look like, what counts as an object, how things are named —
because that is where a source's real structure lives, and forcing sameness there
would only obscure it. Everything *beneath* the layout is uniform; the layout
itself is the connector's to shape.

## Why the line is drawn here

This split is what lets MFS be both broad and small at once. Search, browse,
ranking, the caches, crash recovery, the index — all of it is written against the
one model, so it behaves identically for a source shipped today and one shipped
next year. And a new connector is a thin adapter: project the source onto the
model, declare what it can do, lay out its tree, and the entire engine comes for
free.

It is the same idea that lets a shell operate on countless programs through a few
file operations, and that let data-integration ecosystems grow enormous connector
catalogs: **unify the common part hard, keep the differences thin.** See
[Design philosophy](production.md) for the principles behind it,
[Architecture](architecture.md#core-concepts) for the concrete pieces, and
[Connectors](connectors.md) for the sources themselves.
