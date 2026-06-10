<p align="center">
  <img src="docs/assets/logo.png" alt="MFS logo" width="140" />
</p>

<h1 align="center">MFS — Multi-source File-like Search</h1>

<p align="center">
  <strong>Search files, databases, and SaaS like one filesystem.</strong>
</p>

<p align="center">
  <a href="https://github.com/zilliztech/mfs/blob/main/LICENSE"><img src="https://img.shields.io/github/license/zilliztech/mfs?style=flat-square" alt="License"></a>
  <a href="https://crates.io/crates/mfs-cli"><img src="https://img.shields.io/crates/v/mfs-cli?style=flat-square&color=orange&logo=rust&logoColor=white" alt="crates.io"></a>
  <img src="https://img.shields.io/badge/python-%3E%3D3.10-blue?style=flat-square&logo=python&logoColor=white" alt="Python">
  <a href="https://milvus.io/"><img src="https://img.shields.io/badge/powered%20by-Milvus-00A1EA?style=flat-square" alt="Milvus"></a>
  <a href="https://github.com/zilliztech/mfs/stargazers"><img src="https://img.shields.io/github/stars/zilliztech/mfs?style=flat-square" alt="Stars"></a>
</p>

---

MFS turns any heterogeneous data source — a Postgres database, a Slack
workspace, a Google Drive, a code repo, a Notion graph — into a
**file-like tree under a stable URI**. The same shell verbs work
everywhere: `ls`, `cat`, `tree`, `grep`, `head`, `tail`. Plus one
extra verb: `search`, with hybrid semantic + keyword retrieval built
in.

Defaults run entirely on your laptop. No API keys. No cloud account.

<p align="center">
  <img src="docs/assets/architecture.png" alt="MFS architecture: clients (CLI, SDKs) talk to mfs-server, which unifies many context sources into one searchable namespace" width="880" />
</p>

## Why a file-like shell — not SQL, not GraphQL, not yet-another-SDK?

Because **agents already speak shell**. An LLM agent can plan
`mfs tree` → `mfs search "..."` → `mfs cat --range A:B` with zero
integration code — the same way a human developer would. No client
library to version. No service-specific schema to import. One verb
set, one URI scheme, one JSON envelope, no matter whether the "file"
is a Markdown note, a Postgres row, a Slack thread, or a Gmail
message.

The same CLI an agent uses to **search your context** is the one you
use to **debug what it sees** — `mfs ls`, `mfs cat`, `mfs grep`. No
mystery box between you and the index.

## What you get

- **🗂️ One URI per source.** `postgres://prod-db/users/rows.jsonl`,
  `slack://acme/channels/eng/messages.jsonl`, and
  `~/repo/README.md` all answer to `ls`, `cat`, `search`. Type-erased.
- **🔎 Hybrid search built in.** Dense vectors + BM25 merged in one
  query — no mode picking, no fusion config to tune.
- **🔌 Zero-key local mode.** Defaults are ONNX embeddings + Milvus
  Lite + SQLite. Runs offline; everything under `~/.mfs/`.
- **🤖 Stable JSON envelope.** Search results carry `source` +
  `locator` + `score` + `content`, so an agent chains `search` →
  `cat --locator` without parsing prose.
- **🧩 19 connectors out of the box.** Files, S3, Google Drive,
  Postgres, MySQL, Mongo, BigQuery, Snowflake, GitHub, Jira, Linear,
  HubSpot, Zendesk, Slack, Discord, Gmail, Feishu, Notion, web crawls.

## Install the CLI

```bash
curl --proto '=https' --tlsv1.2 -LsSf \
  https://github.com/zilliztech/mfs/releases/download/v0.4.0-beta.2/mfs-cli-installer.sh | sh
```

Or `cargo install mfs-cli`. Verify with `mfs --version`. Pre-built for
Linux (x86_64 / ARM64) and macOS (Intel / Apple Silicon).

> macOS first launch may prompt about an unidentified developer.
> Run `xattr -d com.apple.quarantine $(which mfs)` once after install.

## Run the server

The CLI is a thin client; indexing and search live in a Python server.
Until the server is on PyPI, run it from source:

```bash
git clone https://github.com/zilliztech/mfs.git
cd mfs/server/python
uv sync
uv run mfs-server run
```

The server binds to `127.0.0.1:13619`. State, the auto-generated bearer
token, and a local Milvus Lite database all live under `~/.mfs/`.
First boot downloads the default embedding model (~600 MB) into
`~/.mfs/onnx-cache/`.

To swap any default — embedding provider, vector backend, storage
layer:

```bash
uv run mfs-server setup
```

Press Enter through every section to keep the local defaults.

## Try it

Index a local folder:

```text
$ mfs add ./my-repo
queued (job 6c281808...). Worker running in background — run `mfs status` to check progress.

$ mfs job show 6c281808...
{
  "status": "succeeded",
  "total_objects": 184,
  "succeeded_objects": 184,
  "failed_objects": 0
}
```

Search across it — semantic + keyword merged in one query:

```text
$ mfs search "rate limit handler" ./my-repo --top-k 3
file://local/my-repo/src/throttle.go  score=0.872
   42  func handleRateLimit(req Request) error {
   43      if exceedsBudget(req.UserID) {
   44          return ErrTooManyRequests

file://local/my-repo/docs/throttling.md  score=0.715
   18  ## Rate-limit handler
   19
   20  Every request goes through `handleRateLimit` before hitting...
```

Reopen exact evidence — never trust just a snippet:

```bash
mfs cat ./my-repo/src/throttle.go --range 42:78
```

The same loop works for any registered source:

```bash
mfs search "renewal risk" hubspot://acme/deals/records.jsonl
mfs grep "401 Unauthorized" slack://acme/channels/oncall
mfs cat postgres://prod/public/tickets/rows.jsonl --locator '{"id":12345}'
```

## The search-and-browse loop

Search finds candidates. Browse turns one candidate into evidence.
Neither leg works on its own.

|  | **Search** | **Browse** |
|---|---|---|
| Commands | `mfs search` · `mfs grep` | `mfs ls` · `mfs tree` · `mfs cat` · `mfs head` · `mfs tail` |
| Shape | Flat ranked hits across the corpus | Walk the natural hierarchy of one object |
| Output | `source` + `locator` + `score` | A directory listing or a byte range |
| Use when | You don't know which file/row/message has the answer | You have a candidate and need exact context |

A typical agent pass alternates the two:

```bash
mfs tree ./my-repo -L 2                # orient
mfs search "session storage" .         # locate candidates
mfs cat ./src/session.go --range 80:140   # confirm before acting
```

> **Search hits are candidates, not evidence.** Reopen with `cat`,
> `cat --range`, or `cat --locator` before you quote, summarize, or
> edit.

## Connectors

Beyond local files, MFS ships 18 more connectors. Each exposes its
source as a URI tree you `ls` / `cat` / `search` like any filesystem:

| Group | Schemes |
|---|---|
| Files & objects | `file`, `s3`, `gdrive` |
| Databases | `postgres`, `mysql`, `mongo`, `bigquery`, `snowflake` |
| Code & issues | `github`, `jira`, `linear` |
| CRM & support | `hubspot`, `zendesk` |
| Chat, mail, docs | `slack`, `discord`, `gmail`, `feishu`, `notion`, `web` |

Probe before adding:

```bash
mfs connector probe linear://workspace --config ./linear.toml
mfs add linear://workspace --config ./linear.toml
```

Per-connector credential setup and TOML shape:
[docs/connector-reference.md](docs/connector-reference.md).

## For agents

Two skill packs drop straight into an agent runtime:

- [`skills/mfs-find`](skills/mfs-find/SKILL.md) — search, grep, browse,
  read across registered sources.
- [`skills/mfs-ingest`](skills/mfs-ingest/SKILL.md) — register a new
  source, update TOML, re-sync, debug ingest.

Every command takes `--json`. The search/grep envelope is stable, so
an agent can chain `mfs --json search ... → mfs --json cat --locator
$LOC` without parsing prose.

<details>
<summary><b>Advanced install — only what you need</b></summary>

```bash
uv sync --extra google              # gdrive + gmail
uv sync --extra slack               # slack
uv sync --extra postgres            # postgres
uv sync --extra all-connectors      # everything

# Optional Rust acceleration for directory walks, hashing, grep, tail
cd server-rs
uv run --project ../server/python maturin develop --release
```

</details>

<details>
<summary><b>Swap defaults — embeddings, vector store, auth</b></summary>

```bash
uv run mfs-server setup --section embedding   # ONNX → OpenAI / Gemini / Ollama / ...
uv run mfs-server setup --section milvus      # Milvus Lite → self-hosted / Zilliz Cloud
uv run mfs-server setup --section auth        # generate / rotate the bearer token
```

Full reference: [docs/configuration.md](docs/configuration.md).

</details>

## Docs

The full guide lives in **[docs/](docs/)** (also served via MkDocs):

- [Quickstart](docs/getting-started.md) — first local run, end to end.
- [Search and Browse](docs/search-and-browse.md) — the search →
  locate → read loop.
- [Connectors](docs/connectors.md) — catalog and per-connector setup.
- [Configuration](docs/configuration.md) — server settings, env vars,
  auth.
- [Deployment](docs/deployment.md) — Docker, Compose, remote server.
- [Troubleshooting](docs/troubleshooting.md) — when things break.

## Roadmap

- Publish `mfs-server` to PyPI for one-command installs.
- OAuth `client_credentials` support for Salesforce and other
  OAuth-only orgs.
- More connectors (Confluence, Asana, Drive shared drives).
- Lock `/v1` HTTP API for the `v0.4.0` final.

## Status

`v0.4.0-beta.2`. CLI and connector surface are stable; the HTTP API
may still shift before `v0.4.0` final, so pin versions in scripts.
Found a bug? Open an issue:
<https://github.com/zilliztech/mfs/issues>.

## Acknowledgements

MFS is shaped by several related projects:

- [claude-context](https://github.com/zilliztech/claude-context) and
  [memsearch](https://github.com/zilliztech/memsearch) — earlier
  Zilliz code-search and memory-search efforts whose community
  feedback shaped MFS's agent-facing direction.
- [VKFS](https://github.com/ZeroZ-lab/vkfs) — a sister exploration
  of a Unix-like interface for agent access to vector-backed
  knowledge.

## License

Apache-2.0. See [LICENSE](LICENSE).
