<h1 align="center"><img src="https://github.com/user-attachments/assets/1a14c3e3-32c3-4474-a081-ce737bfc439a" alt="MFS logo" width="48" align="absmiddle" /> MFS — Multi-source File-like Search</h1>

<p align="center">
  <strong>A context harness for AI agents — and for building them.</strong><br/>
  One shell over your codebases, memory, skills, documents, messages, and every data source you work in.
</p>

<p align="center">
  <a href="https://github.com/zilliztech/mfs/blob/main/LICENSE"><img src="https://img.shields.io/github/license/zilliztech/mfs?style=flat-square" alt="License"></a>
  <a href="https://crates.io/crates/mfs-cli"><img src="https://img.shields.io/crates/v/mfs-cli?style=flat-square&color=orange&logo=rust&logoColor=white" alt="crates.io"></a>
  <img src="https://img.shields.io/badge/python-%3E%3D3.10-blue?style=flat-square&logo=python&logoColor=white" alt="Python">
  <a href="https://milvus.io/"><img src="https://img.shields.io/badge/powered%20by-Milvus-00A1EA?style=flat-square" alt="Milvus"></a>
  <a href="https://github.com/zilliztech/mfs/stargazers"><img src="https://img.shields.io/github/stars/zilliztech/mfs?style=flat-square" alt="Stars"></a>
</p>

---

<p align="center">
  <img src="https://github.com/user-attachments/assets/42c4e26c-c26a-463f-bd97-c5bb2d38eabe" alt="MFS multi-source analysis demo" width="880" />
</p>

Modern AI agents need a place to keep their **context**: codebases,
memory, skills, knowledge, work messages, documents, databases. Most
of that ends up spread across local folders (skill packs, session
memory, your repos, your notes), team SaaS (Slack, Gmail, Notion,
Drive, Feishu), and production stores (Postgres, Mongo, BigQuery,
S3).

MFS gathers it under one shell. Every source — local folders, a
Postgres table, a Slack workspace, a Google Drive, a Notion graph — is
mounted as a **file-like tree under a stable URI**. The shell verbs you
already use work everywhere: `ls`, `cat`, `tree`, `grep`, `head`,
`tail`. Plus `search` for hybrid semantic + keyword retrieval.

<p align="center">
  <img src="https://github.com/user-attachments/assets/1430d872-4184-4fb3-9168-a0b715dc621a" alt="MFS architecture: clients (CLI, SDKs, agent skills) talk to mfs-server, which unifies many context sources into one searchable namespace" width="880" />
</p>

## Install the agent skills

Install the MFS skill packs before asking an agent to search, browse,
or ingest through MFS:

```bash
# Global: available in all projects, all supported agents
npx skills add zilliztech/mfs --all -g

# Project-level: current project only, all supported agents
npx skills add zilliztech/mfs --all
```

<details>
<summary>Install to a specific agent</summary>

```bash
npx skills add zilliztech/mfs -a claude-code -g
npx skills add zilliztech/mfs -a codex -g
```

</details>

<details>
<summary>Check for updates</summary>

```bash
npx skills check
npx skills update
```

For project-level installs, re-run the `npx skills add` command to
update.

</details>

## Quick start

With the skills installed (above) and the server running ([Run it](#run-it)),
point an agent at a folder and ask. **No API key, no GPU, no cloud account** —
the first search downloads a ~600 MB local embedding model into `~/.mfs/`, and
after that the whole stack runs offline.

In your agent (Claude Code, Codex, …) — plain language, or the explicit skill
commands:

```text
You    /mfs-ingest  index ~/notes        ← registers + indexes the folder
You    /mfs-find     what did I decide about the pricing model?
Agent  → searches, opens the top hit, quotes the exact lines, cites the file
```

The same loop straight at the shell, no agent required:

```bash
mfs add ~/notes
mfs search "the pricing model decision" ~/notes --top-k 5
```

That's the whole thing — **ingest a source → search it → read the exact
bytes** — on any folder of text, Markdown, code, or PDFs.

<details>
<summary>Rather use OpenAI than download the local model?</summary>

Set the embedding provider to OpenAI in `~/.mfs/server.toml` (or run
`uv run mfs-server setup`) and export your key — no model download:

```toml
[embedding]
provider = "openai"      # instead of the default local "onnx"
```

```bash
export OPENAI_API_KEY=sk-...
```

</details>

## Use cases

Different sources, same `ingest → search → read` loop. (Outputs are
illustrative — expand to see the shape.)

### One query across every source

`--all` fans a single query across every registered connector at once — local
files, databases, ticket trackers, chat. One stable result shape, so any hit
copies straight into `mfs cat` for the exact evidence.

```bash
mfs search "rate-limit guard misfires under burst" --all
```

<details>
<summary>Output</summary>

```text
slack://acme/channels/oncall/messages.jsonl  score=0.91
  [Mon 22:14] @alice: ratelimiter pegged 500ms p99 tail, dump attached
  [Mon 22:18] @bob:   smells like the burst guard from PR #418

jira://acme/teams/PLAT/issues.jsonl  score=0.83
  PLAT-491  "rate-limit guard misfires under burst"  state=In Progress

file://local/repo/src/throttle.go  score=0.71
  42  func handleRateLimit(req Request) error {
```

Copy any hit into a read — same locators everywhere:

```bash
mfs cat ./repo/src/throttle.go --range 42:78
mfs cat jira://acme/teams/PLAT/issues.jsonl --locator '{"id":"PLAT-491"}'
```

</details>

### Your agent's own memory, skills, and code

Point MFS at the streams an agent project juggles — past-session memory
(Markdown / JSONL), reusable skill packs, the repos it reads — and they become
one searchable namespace. The prompt you tuned last week, a decision logged
three sessions ago, the helper you wrote yesterday: one query finds it.

```bash
mfs add ~/.agents/memory          # past-session JSONL / Markdown
mfs add ~/.agents/skills          # reusable SKILL.md packs
mfs add ~/repos                   # the codebases the agent reads
mfs search "the prompt I tuned for refund disputes" --all
```

<details>
<summary>Output</summary>

```text
file://local/.agents/memory/2026-05-31.jsonl  score=0.88
  {"role":"note","text":"refund-dispute prompt: lead with the order ID, then ..."}

file://local/.agents/skills/support-triage/SKILL.md  score=0.74
  ## Refund disputes — confirm the order ID first, then check the gateway log ...
```

</details>

### Docs, PDFs, and web pages

Drop a folder of PDFs and design docs, or crawl a documentation site — MFS
converts each page to Markdown **locally** (no API key) and indexes the text,
so search reads across all of it.

```bash
mfs add ./design-docs                 # .pdf, .docx, .md — converted locally
mfs search "retention policy for audit logs" ./design-docs
```

<details>
<summary>Output</summary>

```text
file://local/design-docs/data-governance.pdf  score=0.86
  ... Audit logs are retained for 400 days, then moved to cold storage; access
  beyond 90 days requires a break-glass approval ...
```

</details>

### Images, too

With image descriptions turned on, a folder of screenshots or diagrams becomes
searchable by what's *in* them. This one needs a vision model — set
`[description].enabled = true` with a provider in `~/.mfs/server.toml` and
export its key:

```toml
[description]
enabled  = true
provider = "openai"      # gpt-4o-mini by default — needs OPENAI_API_KEY
```

```bash
mfs add ./screenshots
mfs search "the dashboard where p99 latency spikes" ./screenshots
```

<details>
<summary>Output</summary>

```text
file://local/screenshots/grafana-2026-06-02.png  score=0.79
  A Grafana dashboard; the p99 latency panel climbs to ~800 ms around 14:10,
  well above the 200 ms band on the other panels ...
```

</details>


## Run it

The CLI is a thin Rust client; the server holds all the heavy state,
secrets, and workers. Same defaults work two ways — keep both on the
**same machine** (the simplest path, no API key or cloud account
needed) or **split them** for production (server in your data
center / VPC / k8s cluster, CLI and SDKs anywhere your developers
and agents are):

```text
 Local quick-start                       Production
 ─────────────────                       ──────────

  ┌──────────────────┐                    ┌────────────┐     ┌─────────────────────┐
  │  one machine     │                    │   CLI      │     │      mfs-server     │
  │                  │                    │   SDK      │HTTPS│ (VM / container /   │
  │ CLI  ↔  server   │                    │   agent    │─────│  k8s pod, anywhere) │
  │ (shared fs)      │                    │   skills   │     │                     │
  │                  │                    └────────────┘     │  queue · workers    │
  │  ~/.mfs/         │                                       │  Milvus · Postgres  │
  │  Milvus Lite     │                                       │  caches · creds     │
  └──────────────────┘                                       └─────────────────────┘
```

The client is a few-MB Rust binary with no persistent state, so
moving it onto a new laptop, a CI runner, or inside an agent runtime
is free. The server is where the secrets, the index, and the
expensive work live.

### On one machine (60 seconds)

CLI and server share a filesystem so `mfs add ./my-repo` just works
without any upload step. **No API key, no GPU, no cloud account.**
Defaults are local ONNX embeddings + Milvus Lite + SQLite, all
stored under `~/.mfs/`.

```bash
# 1. Install the CLI
curl --proto '=https' --tlsv1.2 -LsSf \
  https://github.com/zilliztech/mfs/releases/download/v0.4.0-beta.2/mfs-cli-installer.sh | sh

# 2. Run the server from source (until it's on PyPI)
git clone https://github.com/zilliztech/mfs.git
cd mfs/server/python
uv sync
uv run mfs-server run

# 3. In another terminal — try it
mfs add ./my-repo
mfs search "rate limit handler" ./my-repo --top-k 5
```

First boot downloads the default embedding model (~600 MB) into
`~/.mfs/onnx-cache/`. After that the local stack is fully offline.

> macOS first launch may prompt about an unidentified developer. Run
> `xattr -d com.apple.quarantine $(which mfs)` once after install.

### Split across machines (production)

Server-side configuration is the same in both modes — the wizard walks
through embedding provider, vector backend, database, cache, and auth
(see [Configure the server](#configure-the-server-wizard-or-toml)
below for what it looks like). For deeper knobs, edit
`~/.mfs/server.toml` directly.

```bash
uv run mfs-server setup                          # walk the wizard on the server

export MFS_API_URL=https://mfs.your-corp.internal   # point the CLI at the remote server
export MFS_API_TOKEN=...
mfs status
```

Docker images, a Compose file, and a Helm chart for split
api / worker deployments live under
[`deployments/`](deployments/).

## How the C / S split works

| On the client | On the server |
|---|---|
| `mfs` CLI (Rust, 2–4 ms cold start, ~6 MB binary) | All connector credentials, env vars, and TOML config |
| Generated SDKs (Python, TypeScript) | Queue + workers, indexing jobs |
| Agent skill packs (`mfs-find`, `mfs-ingest`) | Metadata DB (SQLite or Postgres) |
| Endpoint / profile / token resolution | Vector index (Milvus Lite, self-hosted Milvus, or Zilliz Cloud) |
| Output rendering | Artifact + transformation caches |
| | Embedding, VLM, summary, chunking, conversion |

Client and server can sit on the **same machine** (the quick-start
mode above) or on **different machines** (production mode). The client
is nearly stateless, so re-creating it on a new laptop, in a Docker
image, or inside an agent runtime is free. The server is where the
state, the secrets, and the expensive work live.

## Configure the server: wizard or TOML

The interactive wizard walks six sections — defaults are
self-contained, press Enter through to keep them:

```text
MFS server setup
  writing to ~/.mfs/server.toml
  6 section(s): embedding · image-summary · milvus · database · cache · auth

╭─ Step 1/6 · Embedding ───────────────────────────────────────────────╮
│  Default is local ONNX (no API key, BGE-M3 int8, ~600 MB download). │
│  Pick another provider to opt out.                                  │
╰─────────────────────────────────────────────────────────────────────╯
? Provider (↑↓ to move · Enter to confirm)
 » onnx       local, no API key (default)
   openai     needs OPENAI_API_KEY env
   gemini     needs `uv sync --extra gemini`
   voyage     needs `uv sync --extra voyage`
   ollama     needs `uv sync --extra ollama` + running ollama server
   local      needs `uv sync --extra local` (pulls torch ~2 GB)

╭─ Step 3/6 · Milvus (vector DB) ─────────────────────────────────────╮
│  Default = Milvus Lite (a file under $MFS_HOME). Switch to remote   │
│  Milvus / Zilliz Cloud by supplying the URI.                        │
╰─────────────────────────────────────────────────────────────────────╯
? Backend  lite  ·  remote-milvus  ·  zilliz-cloud
```

Run a single section any time:

```bash
uv run mfs-server setup --section embedding
```

For advanced knobs (cache size, eviction policy, chunker thresholds,
namespace, custom worker count) — edit `~/.mfs/server.toml`
directly. See [docs/configuration.md](docs/configuration.md) for the
full field reference.

## Connectors

Beyond local files, MFS ships a growing catalog of connectors. Each
exposes its source as a URI tree you can `ls` / `cat` / `search` like
a directory:

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

## 🛠️ Build agents on top of MFS

If you're building an agent project (not just calling MFS from a
shell), MFS becomes the harness — the retrieval and context layer
your agent sits on top of, not a passive index it queries
occasionally.

A modern agent project juggles several streams of state at once:

- **Memory** — past sessions, recaps, decision logs, scratch notes
- **Skills** — reusable `SKILL.md` packs, prompts, runbooks
- **Code** — every repo the agent reads or writes
- **Knowledge** — docs, PDFs, design specs, meeting transcripts
- **Work signals** — Slack threads, emails, tickets, CRM records,
  database state, dashboards

Without a harness this spreads across local folders, SaaS apps, and
private databases. With MFS the agent gets one CLI surface over all
of it — and you skip writing a connector per source.

Three ways to wire MFS into your agent:

- **🧩 Skill packs.** Drop [`skills/mfs-find`](skills/mfs-find/SKILL.md)
  and [`skills/mfs-ingest`](skills/mfs-ingest/SKILL.md) into your
  coding agent runtime, and the agent
  inherits the whole search-and-browse loop with no custom
  integration code.
- **📦 SDKs.** Generated Python and TypeScript clients under `sdks/`
  cover the cases where shelling out to `mfs` is awkward (long-
  running daemons, language runtimes without a shell).
- **🔗 HTTP `/v1`.** Skills and SDKs are thin wrappers around the
  same OpenAPI surface — go direct when you need to.

## 🛡️ Robust by design

The index is **derived state** — losable, rebuildable, crash-safe:

- **🔁 Rename detection in three tiers** — `(size, mtime)` first,
  then inode pairing for same-filesystem moves, then sha1 fallback
  for cross-filesystem / Windows / git-rewrite cases. Moving or
  renaming files costs **zero embedding API calls**.
- **💾 Content-addressable cache.** Embeddings, conversions,
  summaries are keyed by `sha1(content + tool + version)` —
  survives `git checkout`, vector-DB rebuilds, and embedding-model
  rollbacks with cache hits.
- **♻️ Idempotent everything.** `chunk_id` is a content hash; writes
  are `DELETE + INSERT`. No `mfs retry`, no `mfs resume` — recovery
  collapses to *"crash → just rerun `mfs add`"*.
- **🚫 Three-layer ignore.** Built-in defaults + `.gitignore` +
  `.mfsignore`. Ignored files don't even become MFS objects.

Full mechanics: [docs/architecture.md](docs/architecture.md).

## 💭 Why it works the way it does

Three principles run through the architecture:

- **Upstream is the source of truth.** MFS keeps a derived index;
  delete `~/.mfs/` and you lose no data — `mfs add` rebuilds from
  the original sources.
- **Search × browse — two legs of one loop.** Like a library: point
  at the shelf, flip pages, read the right one. Never trust a search
  hit until you've reopened it.
- **File-like URIs because agents already speak shell.** No new
  query language, no per-source SDK. The same handful of verbs cover
  every connector.

Full design notes: [docs/design-philosophy.md](docs/design-philosophy.md).

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
- OAuth `client_credentials` for Salesforce and OAuth-only orgs.
- More connectors (Confluence, Asana, Drive shared drives).
- Lock `/v1` HTTP API for the `v0.4.0` final.

## Status

`v0.4.0-beta.2`. The CLI surface and connector catalog are stable;
the HTTP API may still shift before `v0.4.0` final, so pin versions
in scripts. Found a bug? Open an issue:
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
