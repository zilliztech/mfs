<p align="center">
  <img src="docs/assets/logo.png" alt="MFS logo" width="140" />
</p>

# MFS — Multi-source File-like Search

**Search files, databases, and SaaS like one filesystem.**

MFS turns any heterogeneous data source — a code repo, a Postgres
database, a Slack workspace, a Google Drive — into a file-like tree
under a stable URI. The same shell verbs work everywhere: `ls`, `cat`,
`tree`, `grep`, `head`, `tail`, plus `search` for hybrid semantic +
keyword retrieval.

Defaults run entirely on your laptop. No API keys, no cloud account.
Swap any layer for a hosted backend when you outgrow it.

<p align="center">
  <img src="docs/assets/architecture.png" alt="MFS architecture: clients (CLI, SDKs) talk to mfs-server, which unifies many context sources (memory, skills, knowledge, messages, email, customers, project work, data records) into one searchable namespace" width="880" />
</p>

## Why MFS

- **One URI per source.** `postgres://prod-db/users/rows.jsonl` and
  `~/repo/README.md` get the same `ls`, `cat`, `search` treatment.
- **Hybrid search built in.** BM25 + dense-vector results merged in one
  query — no mode picking.
- **Zero-key local mode.** Defaults are ONNX embeddings + Milvus Lite +
  SQLite. Runs offline.
- **Designed for agents.** Every command takes `--json`; search results
  carry a stable `source` + `locator` + `score`, so an agent can chain
  `search` → `cat --locator` without parsing prose.

## Install the CLI

```bash
curl --proto '=https' --tlsv1.2 -LsSf \
  https://github.com/zilliztech/mfs/releases/download/v0.4.0-beta.2/mfs-cli-installer.sh | sh
```

Or `cargo install mfs-cli --version 0.4.0-beta.2`.

Verify with `mfs --version`. Pre-built for Linux (x86_64 / ARM64) and
macOS (Intel / Apple Silicon).

> macOS first launch may prompt about an unidentified developer. Run
> `xattr -d com.apple.quarantine $(which mfs)` once after install, or
> allow it in System Settings → Privacy & Security.

## Run the server

The CLI is a thin client; indexing and search live in a Python server.
During the beta, run it from source:

```bash
git clone https://github.com/zilliztech/mfs.git
cd mfs/server/python
uv sync
uv run mfs-server run
```

The server binds to `127.0.0.1:13619`. Config, state, an auto-generated
bearer token, and a local Milvus Lite database all live under `~/.mfs/`.
First boot downloads the default embedding model (~600 MB) into
`~/.mfs/onnx-cache/`.

To swap any default — embedding provider, vector backend, storage layer:

```bash
uv run mfs-server setup
```

Press Enter through every section to keep the local defaults.

## Try it

With the server running, in another terminal:

```bash
mfs add ./my-repo
mfs job show <JOB_ID>     # wait until status is "succeeded"

mfs search "rate limit handler" ./my-repo --top-k 5
mfs cat ./my-repo/src/throttle.go --range 42:78
```

`mfs add` returns a job id immediately; the worker indexes in the
background.

Search results are candidates, not evidence — reopen with
`mfs cat --range` or `mfs cat --locator` before you trust them.

## Connectors

Beyond local files, MFS includes 18 more connectors. Each exposes its
source as a URI tree you can `ls`/`cat`/`search` like any filesystem:

| Group | Schemes |
|---|---|
| Files & objects | `file`, `s3`, `gdrive` |
| Databases | `postgres`, `mysql`, `mongo`, `bigquery`, `snowflake` |
| Code & issues | `github`, `jira`, `linear` |
| CRM & support | `hubspot`, `zendesk` |
| Chat, mail, docs | `slack`, `discord`, `gmail`, `feishu`, `notion`, `web` |

Each connector has its own credentials and TOML shape. Probe before
adding:

```bash
mfs connector probe linear://workspace --config ./linear.toml
mfs add linear://workspace --config ./linear.toml
```

Per-connector setup: [docs/connector-reference.md](docs/connector-reference.md).

## For agents

Two skill packs drop directly into an agent runtime:

- [`skills/mfs-find`](skills/mfs-find/SKILL.md) — search, grep, browse,
  read across registered sources.
- [`skills/mfs-ingest`](skills/mfs-ingest/SKILL.md) — register a new
  source, update TOML, re-sync, debug ingest.

## Docs

Full guide at **[docs/](docs/)** (also served via MkDocs):

- [Quickstart](docs/getting-started.md) — first local run, end to end.
- [Search and Browse](docs/search-and-browse.md) — the search → locate →
  read loop.
- [Connectors](docs/connectors.md) — connector catalog and config.
- [Configuration](docs/configuration.md) — server settings, env vars,
  auth.
- [Deployment](docs/deployment.md) — Docker, Compose, remote server.
- [Troubleshooting](docs/troubleshooting.md) — when things break.

## Status

This is **`v0.4.0-beta.2`**, a public beta:

- ✅ CLI: stable surface for documented commands.
- ✅ Server: 19-scheme connector matrix, hybrid search, incremental
  sync.
- ⚠ Distribution: only the CLI is published. Server and SDKs run from
  source.
- ⚠ The HTTP API may still shift before `v0.4.0` stable — pin the
  version in scripts.

Found a bug? Open an issue: <https://github.com/zilliztech/mfs/issues>.

## License

Apache-2.0. See [LICENSE](LICENSE).
