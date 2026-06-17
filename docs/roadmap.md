# Status and limits

MFS is beta software under active development. The core loop — register a source,
index it, search and browse it — is solid and used daily. This page is the honest
summary of what's stable, what's still rough, and what isn't here yet, so you can
decide what to rely on.

!!! warning "Pin what you test"
    During the beta, pin the CLI and `mfs-server` versions you're evaluating and
    keep them in step. Verify any API or SDK integration against the running
    server, not just the generated client docs.

## What's stable

- **The CLI and the loop.** `mfs add`, `search`, `grep`, `ls`, `tree`, `cat`,
  `head`, `tail`, `export`, and the `connector` / `job` subcommands are the
  steady surface. Examples in these docs track them.
- **Local first run.** Install the CLI, `uv tool install mfs-server`, index a
  folder — the defaults (ONNX embeddings, Milvus Lite, SQLite, a generated token)
  work offline with no cloud account.
- **The `/v1` control plane.** `protocol/openapi.yaml` is the source of truth for
  endpoints and schemas, and both SDKs are generated from it. When auth is
  configured, every request except `GET /healthz` needs a bearer token.
- **Connectors.** `file` is always available; the rest load when their
  dependencies are installed. Probe on the target server before a large sync.

## What's still beta

- **SDK coverage.** The generated Python and TypeScript clients cover the common
  server, ingest, retrieval, and browse calls. A few endpoints (connector
  management, the file manifest/upload steps, `head`/`tail`/`export`, job
  listing) are in the OpenAPI surface but not yet surfaced as generated methods —
  call `/v1` directly for those.
- **API stability.** The HTTP API may still shift before a stable release. Pin
  versions for scripts and integrations.

## Scaled deployment

Running fully local is a first-class, supported path. The single-host container
(Docker / Compose all-in-one) is also runnable today. A horizontally scaled
split — separate API and worker processes against externalized Postgres, object
storage, and a managed Milvus/Zilliz endpoint — is the documented direction the
architecture is built for; treat the Helm chart as that target rather than a
turnkey default. See [Deployment](deployment.md).

## Where MFS draws the line

A couple of things MFS deliberately is *not*, so you reach for the right tool:

- It's not a mounted filesystem — no POSIX writes, locks, or kernel semantics. It
  adds search, browse, and read surfaces over sources.
- It's not a vector database for your own app. It uses Milvus for its own index;
  talk to Milvus or Zilliz directly if you need a vector store.

See [Why MFS](why.md) for when it fits and when it doesn't.

## What's ahead

Directional, not dated:

- **Multiple processing profiles** — per-source pipelines, each its own
  collection, so a code source and a multilingual document source can use
  different embedding models.
- **Multi-user credentials and access control** — so a hosted deployment can let
  each user bring their own sources and secrets.
- **A wider connector catalog.**
