# MFS — Multi-source File-like Search

> Agent-native file search CLI for large local workspaces, ideal for managing memory, skill, codebase and knowledgebase.

MFS exposes any heterogeneous data source — a codebase, a docs site, a
database, a SaaS workspace — through the same shell verbs you already use
on the filesystem: `ls`, `cat`, `tree`, `head`, `tail`, `grep`. On top of
that, `mfs search` runs hybrid semantic + literal retrieval across one or
many sources at once.

It was designed to be the search/read surface for AI agents. Every command
returns predictable structured output so an agent (or a human) can chain
search → locate → browse without parsing prose.

```
        search                locate                  browse
  ┌──────────────────┐   ┌──────────────────┐   ┌─────────────────────┐
  │  semantic + BM25 │ → │ result has lines │ → │  cat --range / cat  │
  │  finds candidate │   │  or a locator    │   │  --peek to confirm  │
  │  files / rows    │   │  → reopen exact  │   │  context            │
  └──────────────────┘   └──────────────────┘   └─────────────────────┘
```

This is the **`v0.4.0-beta.2` release** — an early-access build. The CLI is
shipped as a static binary; the server runs in dev mode from this repo.
See [Status](#status) for what's stable and what isn't.

## Install the CLI

```bash
curl --proto '=https' --tlsv1.2 -LsSf \
  https://github.com/zilliztech/mfs/releases/download/v0.4.0-beta.2/mfs-cli-installer.sh | sh
```

(The script name carries the crate name `mfs-cli`; the installed binary is `mfs`.)

Or via cargo:

```bash
cargo install mfs-cli --version 0.4.0-beta.2
```

The binary is named `mfs`. Verify:

```bash
mfs --version
```

**Pre-built platforms**:

- Linux x86_64 (musl, static)
- Linux ARM64 (musl, static)
- macOS x86_64 (Intel)
- macOS ARM64 (Apple Silicon)

> **macOS note**: the binary is **not yet code-signed**, so the first launch
> may prompt "unidentified developer". Either allow it in System Settings →
> Privacy & Security, or run `xattr -d com.apple.quarantine $(which mfs)`
> once after install.

## Run the server (dev mode)

The server is a Python FastAPI app. For `v0.4.0-beta.2` it is **not** published
to PyPI — clone the repo and run it from source:

```bash
git clone https://github.com/zilliztech/mfs.git
cd mfs/server/python

# uv installs all required deps into a local venv
uv sync

# Optional: walk a 6-section wizard that writes ~/.mfs/server.toml. Every
# section defaults to a self-contained local backend (ONNX embeddings,
# Milvus Lite, SQLite, local fs). Plug in OpenAI / Zilliz Cloud / Postgres
# only when you have credentials.
uv run mfs-server setup            # all sections, press Enter to accept defaults
uv run mfs-server setup --section embedding   # change a single section later

# starts on 127.0.0.1:13619 by default (matches the CLI's default endpoint).
# First `mfs add` downloads the multilingual BGE-M3 int8 ONNX model into
# $MFS_HOME/onnx-cache/ (one-time, ~600 MB).
uv run mfs-server run
```

**Default backends are zero-key** — out of the box:

| What | Default |
|---|---|
| Embedding | local ONNX, `gpahal/bge-m3-onnx-int8` — multilingual, 1024-dim (no API key) |
| VLM / image summary | OFF (opt-in via `mfs-server setup --section vlm`) |
| Vector DB | Milvus Lite (file under `$MFS_HOME`) |
| Metadata DB | SQLite (file under `$MFS_HOME`) |
| Object store | Local filesystem (under `$MFS_HOME`) |
| API auth | Auto-generated Bearer token at `$MFS_HOME/server.token` |

Want to switch to OpenAI embeddings / Zilliz Cloud / Postgres / S3? Re-run
`mfs-server setup --section <name>` and pick a different backend.

**Connector extras** (optional — install only what you need):

```bash
uv sync --extra pg          # postgres
uv sync --extra slack       # Slack
uv sync --extra all-connectors   # everything
```

**Optional Rust hot-path acceleration** (`server-rs`): the server transparently
uses a Rust extension for directory walks, parallel hashing, grep and tail
when available. Without it, it falls back to pure Python — identical
behaviour, just slower on big inputs. To install:

```bash
cd server-rs
uv run --project ../server/python maturin develop --release
```

## Try it

With the server running on `127.0.0.1:13619`:

```bash
mfs status                         # server up? connectors registered?
mfs add --wait ./my-repo           # index a local directory and wait for completion

mfs search "rate limit handler" ./my-repo --top-k 5
mfs cat ./my-repo/src/throttle.go --range 42:78
```

If you omit `--wait`, `mfs add` returns a queued job id:

```bash
mfs add ./my-repo
mfs job show JOB_ID
```

Beyond `file://`, MFS ships connectors for postgres, mysql, snowflake,
mongo, github, jira, hubspot, salesforce, notion, zendesk, slack, discord,
gmail, feishu, s3, web — twenty schemes in total. Run `mfs connector list`
for registered sources and `mfs --help` for the full CLI surface.

Use the MkDocs guides when you need the full runbook:

| Guide | Start here for |
|---|---|
| [Quickstart](docs/getting-started.md) | First local run, checkpoints, and upload mode. |
| [CLI Reference](docs/cli.md) | Current command forms, flags, jobs, and profiles. |
| [Search and Browse](docs/search-and-browse.md) | Search, reopen exact evidence, and read narrow ranges. |
| [Connectors](docs/connectors.md) | Connector catalog, TOML config, credentials, and lifecycle. |
| [Configuration](docs/configuration.md) | Server defaults, auth, endpoint, token, and profile precedence. |
| [Providers and Processing](docs/providers.md) | Embedding providers, setup extras, first-run cache behavior, and VLM/summary processing. |
| [Deployment](docs/deployment.md) | Source, Docker, Compose, and beta deployment boundaries. |
| [Troubleshooting](docs/troubleshooting.md) | Endpoint, auth, upload, indexing, and browse failures. |
| [Development](docs/development.md) | Package boundaries, local setup, checks, and OpenAPI-to-SDK regeneration. |

## For agents

If you're an agent reading this, use the matching skill for the operation:

- [`skills/mfs-find/SKILL.md`](skills/mfs-find/SKILL.md) for read-only search,
  grep, browse, and cat workflows over registered sources.
- [`skills/mfs-ingest/SKILL.md`](skills/mfs-ingest/SKILL.md) for adding or
  updating sources, connector configuration, and ingest troubleshooting.

## Status

`v0.4.0-beta.2` is a **public beta** intended for evaluation and feedback.
Concretely:

- ✅ CLI: stable surface for the documented commands.
- ✅ Server: 20-scheme connector matrix, hybrid search, rename detection,
  incremental sync.
- ⚠ Distribution: only the CLI is published. Server / SDK / Rust wheel
  run from source.
- ⚠ The HTTP API may still shift before `v0.4.0` stable — pin the version
  in any scripts you write against the beta.

Found a bug? Surprising behaviour? Open an issue at
https://github.com/zilliztech/mfs/issues.

## Docs

- [`docs/`](docs/) — MkDocs source for the public documentation site
- [`mkdocs.yml`](mkdocs.yml) — documentation site structure and theme config
- [`skills/mfs-find/SKILL.md`](skills/mfs-find/SKILL.md) — agent search and
  browse workflow
- [`skills/mfs-ingest/SKILL.md`](skills/mfs-ingest/SKILL.md) — agent ingest and
  connector workflow
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — dev setup, testing, lint, commit /
  PR conventions

## License

Apache-2.0. See [`LICENSE`](LICENSE).
