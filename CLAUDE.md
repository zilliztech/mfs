# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

MFS exposes many sources (code, docs, messages, databases, object stores) as **one
file-like, searchable namespace**. It is a monorepo of two cooperating processes:

- **`cli/`** — a thin **Rust** CLI (`mfs`). It is *only* an HTTP client of the
  server; it holds no business logic. Don't add behavior here that belongs on the
  server.
- **`server/python/`** — the **Python** server (`mfs-server`, FastAPI). It owns
  connectors, the ingest pipeline, the vector index, and storage, exposed over an
  HTTP `/v1` control plane. This is where almost all work happens.

The SDKs (`sdks/python`, `sdks/typescript`) are generated from
`protocol/openapi.yaml`. `server-rs/` is an optional PyO3 acceleration wheel with a
transparent pure-Python fallback (`mfs_server.common.accel`) — never required.

`CONTRIBUTING.md` and `docs/architecture.md` are the long-form references; the
points below are the ones that need several files to piece together.

## Commands

All Python commands run from `server/python/`. **`dev` is an extra, not a
dependency-group** — `uv run pytest` fails (no pytest); always use `--extra dev`:

```bash
uv sync                                   # default deps (local ONNX, Milvus Lite, SQLite)
uv run --extra dev pytest                 # all unit/smoke tests
uv run --extra dev pytest tests/test_engine_chunkable_e2e.py::test_chunkable_path_drains_through_pipeline   # one test
uv run --extra dev ruff format --check src/ tests/    # what CI enforces
uv run --extra dev ruff check src/ tests/             # lint (loose; not gated in CI)
uv run mfs-server run                     # run server from checkout, binds 127.0.0.1:13619
```

- A few tests import optional connector SDKs (e.g. `lark_oapi`) and **fail to
  collect** without them; `--ignore` those files or `uv sync --extra all-connectors`.
- CI (`.github/workflows/lint.yml`) enforces **formatting only** — `ruff format
  --check` and `cargo fmt --all -- --check`. Tests are not gated, so run them yourself.

Rust:

```bash
cd cli && cargo build --release && cargo test
cargo fmt --manifest-path cli/Cargo.toml --all -- --check       # CI format-checks cli
cargo fmt --manifest-path server-rs/Cargo.toml --all -- --check # ...and server-rs
```

All server state (config, token, SQLite metadata, Milvus Lite vectors, caches, and
the ONNX model cache) lives under one root, `$MFS_HOME` (default `~/.mfs`). The
vector and metadata backends are selectable by environment/config (Milvus Lite vs a
remote Milvus/Zilliz; SQLite vs Postgres) — see `docs/deployment.md`.

## Architecture that spans files

**Connectors → path tree.** Each connector (`server/python/src/mfs_server/connectors/<name>/plugin.py`)
presents an external system as a path tree and classifies each object's
`object_kind`; it never touches the index, embeddings, or cache directly. The
contract is `connectors/base.py` (`ConnectorPlugin`): 5 abstract methods
(`stat` / `list` / `fingerprint` / `sync` / `object_kind_of`) plus `read` **or**
`read_records`, with optional overrides (`grep`, `search`, `preset_for`, …).
Registration is `register(MyPlugin)` in the connector's `__init__.py` plus an entry
in `registry.py:load_builtin()` — there is no central `REGISTRY` dict.

**`object_kind` and `chunk_kind` are framework-fixed `Literal`s** in `base.py`.
Adding a value is a framework change (RFC), not a connector change.

**Ingest runs two lanes** (`engine/`, see `docs/ingest-pipeline.md`):
- *Object Lane* — each object goes producer → in-memory chunk queue → a
  **process-shared `EmbedConsumer`** that embeds + upserts chunks to Milvus, then a
  finalize hook (`_on_pipeline_object_indexed`) writes the `objects` metadata row.
- *Job Lane* — cross-object work (directory summaries) that can't be done per-object.

The consumer is decoupled and shared across jobs; this is the subtle part. The core
invariant is **a chunk exists in Milvus iff a committed `objects` row points at it**
(directory summaries are the deliberate exception — they have no `objects` row).
Cancellation/removal and a startup GC reconcile against this invariant, so any new
finalize/cleanup path must preserve it.

**Storage** (`storage/`): metadata in SQLite or Postgres (connectors, objects,
jobs, and the queues — *a queue is just a table*), vectors in Milvus
(`connector_uri` is the partition key), an artifact cache, and a separate
transformation cache (model outputs). Shapes: `docs/schema.md`, `docs/caching.md`.

## Connector config: four sources, one source of truth

A connector's config/credentials are described in four places that must stay
consistent; **the code is the source of truth**, the rest document it at increasing
detail (skill < wizard < docs):

1. **Code** — `connectors/<name>/plugin.py` (keys it reads) + the user's `connector.toml`.
2. **Wizard** — `server/connector_schemas.py` (an important-fields subset).
3. **Skill** — `skills/mfs-ingest/reference/connectors/<name>.md` (short, for agents).
4. **Docs** — `docs/connectors/<name>.md` (full human walkthrough).

When you touch one, grep the connector name across the other three and reconcile;
field names and enum values must match the plugin exactly. Secrets are never
plaintext in TOML — the TOML carries an `env:VAR` / `file:/abs/path` reference that
the server resolves at plugin-build time; a plugin reads credentials only through
the framework, never `os.environ[...]`.

## Releasing

Versions are **lockstep** across `mfs-cli`, `mfs-server`, the SDKs, `server-rs`, and
the `deployments/` image tags (Dockerfile comment + docker-compose.yml). A single
`v*` tag triggers three publish workflows (`release.yml` → CLI binaries + GitHub
Release, `publish-crates.yml` → crates.io, `publish-pypi.yml` → PyPI via Trusted
Publishing). `server-rs` is deliberately **not** published to PyPI. Merging to
`main` does not publish; pushing the tag does.

The bump is **manual + CI-verified, not auto-written**: a human edits every version
string by hand, then `lint.yml`'s `version-lockstep` job is the single source of
truth for which files must agree — read that job to get the exact current list
rather than trusting a stale list here. Bump every file it checks, open a PR titled
`release: cut X.Y.Z`, merge, then tag the merge commit and push the tag — that push
is the only irreversible step (it publishes to PyPI/crates.io).

The next version number is **not** auto-inferred from `feat:` commit titles alone —
`release-drafter` only bumps minor/major off an explicit `minor`/`breaking` label a
maintainer adds by hand; routine `feat:` PRs default to a patch bump so 0.4.x stays
small and incremental.

## Skills

`skills/` holds two agent skills — `mfs-ingest` (register/update sources) and
`mfs-find` (search/browse). They are **agent-agnostic**: describe intent + the `mfs`
CLI/API, never couple to a specific agent's tools or UI.
