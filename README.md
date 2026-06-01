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

This is the **`v0.4.0-beta.1` release** — an early-access build. The CLI is
shipped as a static binary; the server runs in dev mode from this repo.
See [Status](#status) for what's stable and what isn't.

## Install the CLI

```bash
curl --proto '=https' --tlsv1.2 -LsSf \
  https://github.com/zilliztech/mfs/releases/download/v0.4.0-beta.1/mfs-cli-installer.sh | sh
```

(The script name carries the crate name `mfs-cli`; the installed binary is `mfs`.)

Or via cargo:

```bash
cargo install mfs-cli --version 0.4.0-beta.1
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

The server is a Python FastAPI app. For `v0.4.0-beta.1` it is **not** published
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
mfs status                      # server up? connectors registered?
mfs add ./my-repo               # register a directory, indexes in the background
mfs status file://my-repo       # poll until 'available'

mfs search "rate limit handler" --connector-uri file://my-repo --top-k 5
# Hit returns: file path + line range
mfs cat file://my-repo/src/throttle.go --range 42:78
```

Beyond `file://`, MFS ships connectors for postgres, mysql, snowflake,
mongo, github, jira, hubspot, salesforce, notion, zendesk, slack, discord,
gmail, feishu, s3, web — twenty schemes in total. Run `mfs connector ls`
for the registered catalog and `mfs --help` for the full CLI surface.

## For agents

If you're an agent reading this, the matching SKILL is at
[`skills/mfs/SKILL.md`](skills/mfs/SKILL.md). Connector-specific reference
material is in [`skills/mfs/references/`](skills/mfs/references/) — load
each file only when the situation matches its "Open WHEN" line.

## Status

`v0.4.0-beta.1` is a **public beta** intended for evaluation and feedback.
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

- [`skills/mfs/SKILL.md`](skills/mfs/SKILL.md) — agent skill (also the most
  concise human reference for the CLI workflow)
- [`skills/mfs/references/`](skills/mfs/references/) — per-connector "Open
  WHEN" pages: URI shape, auth, config, gotchas
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — dev setup, testing, lint, commit /
  PR conventions

## License

Apache-2.0. See [`LICENSE`](LICENSE).
