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

## 🚀 Quick start

Install the skill packs once:

```bash
# every project + every supported agent (drop -g for the current project only)
npx skills add zilliztech/mfs --all -g
```

Then open your agent (Claude Code, Codex, …) and ask in plain language. First,
ingest something:

```text
Spin up a tiny hello-world project in ~/hello-mfs, then ingest it with mfs
```

Then search and read across it:

```text
Search the hello-mfs project for where the greeting is printed, and show me the
exact lines
```

That's the whole loop — **ingest → search → read**.

> 🛠️ **The first run is a one-time setup, and the agent walks you through it:**
> it installs the `mfs` CLI, helps you start a local server, and downloads a
> ~600 MB local embedding model into `~/.mfs/`. Give it a minute. After that the
> whole stack runs locally and offline — **no API key, no GPU, no cloud account.**

<details>
<summary>Install to one agent · check for updates</summary>

```bash
npx skills add zilliztech/mfs -a claude-code -g    # or: -a codex
npx skills check && npx skills update              # re-run `npx skills add` for project installs
```

</details>

<details>
<summary>Prefer the shell, no agent?</summary>

Run a local server, install the CLI, and drive the same loop directly:

```bash
# server — from source until it's published
git clone https://github.com/zilliztech/mfs.git
cd mfs/server/python && uv sync && uv run mfs-server run

# CLI — `cargo install mfs-cli`, or the installer on the releases page
mfs add ~/hello-mfs
mfs search "where the greeting is printed" ~/hello-mfs
```

> macOS: run `xattr -d com.apple.quarantine $(which mfs)` once if prompted about
> an unidentified developer.

</details>

<details>
<summary>Use OpenAI instead of the local model?</summary>

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

## 💡 Use cases

Every source rides the same **ingest → search → read** loop. The outputs below
are illustrative — expand each to see the result shape.

### 🧠 Your agent's memory and skills

Point MFS at the local streams an agent project juggles — past-session memory
(Markdown / JSONL) and reusable skill packs — and they collapse into one
searchable namespace. The prompt you tuned last week, a decision logged three
sessions ago: one query finds it.

```bash
mfs add ~/.agents/memory          # past-session JSONL / Markdown
mfs add ~/.agents/skills          # reusable SKILL.md packs
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

### 💻 Your codebases

Add every repo the agent reads or writes and grep them by meaning — find the
helper by what it *does*, not the name you can't remember.

```bash
mfs add ~/repos
mfs search "where do we retry failed webhook deliveries?" --all
```

<details>
<summary>Output</summary>

```text
file://local/repos/payments/webhooks/deliver.go  score=0.84
  87  // cap exponential backoff at 6 attempts, then dead-letter
  88  func (d *Dispatcher) retryDelivery(ev Event) error {

file://local/repos/payments/webhooks/deliver_test.go  score=0.69
  TestRetryDelivery_DeadLettersAfterMaxAttempts ...
```

</details>

### 📄 Documents, any format

Drop a folder of PDFs, Word docs, Markdown, and screenshots. MFS converts each
file to text **locally** — PDF / docx → Markdown, no API key — and, with a
vision model turned on, describes images too, so one search reads across every
format and modality at once.

```bash
mfs add ./design-docs            # .pdf, .docx, .md — converted locally
mfs add ./screenshots            # .png, .jpg — needs a vision model (see note)
mfs search "audit-log retention and the dashboards that show it" --all
```

<details>
<summary>Output</summary>

```text
file://local/design-docs/data-governance.pdf  score=0.86
  ... Audit logs are retained for 400 days, then moved to cold storage; access
  beyond 90 days requires a break-glass approval ...

file://local/screenshots/grafana-2026-06-02.png  score=0.71
  A Grafana dashboard; the p99 latency panel climbs to ~800 ms around 14:10,
  well above the 200 ms band on the other panels ...
```

</details>

> 🖼️ Image search needs image descriptions on: set `[description].enabled = true`
> with a provider in `~/.mfs/server.toml` and export its key.

### 🌍 Online sources

Crawl a documentation site or mount a GitHub repo with its issues — remote
content lands in the same namespace as your local files, with no manual
download step.

```bash
mfs add web://docs.your-product.com
mfs add github://your-org/your-repo --config ./github.toml
mfs search "how do we rotate signing keys?" --all
```

<details>
<summary>Output</summary>

```text
web://docs.your-product.com/security/key-rotation  score=0.88
  ... Signing keys rotate every 90 days. Trigger an early rotation from the
  admin console; the previous key stays valid for a 24-hour overlap ...

github://your-org/your-repo/issues.jsonl  score=0.75
  #312  "Automate signing-key rotation"  state=open  labels=[security]
```

</details>

### 💬 Team chat and tickets

Mount Slack, Gmail, Jira, Linear — the conversational trail behind a decision —
and pull the thread, the ticket, and the email into a single answer.

```bash
mfs add slack://acme --config ./slack.toml
mfs add jira://acme  --config ./jira.toml
mfs search "why did we revert the burst guard?" --all
```

<details>
<summary>Output</summary>

```text
slack://acme/channels/platform/messages.jsonl  score=0.90
  [Tue 09:40] @carol: reverting the burst guard — it dropped healthy traffic
  [Tue 09:42] @dave:  agreed, reopening PLAT-491 to re-tune the window

jira://acme/teams/PLAT/issues.jsonl  score=0.81
  PLAT-491  "rate-limit guard misfires under burst"  state=Reopened
```

</details>

### 🗄️ Production data

Point MFS at Postgres, Mongo, or BigQuery and search rows as text. Each row is
a file-like object, so `mfs cat` pulls back the full record for the exact
values.

```bash
mfs add postgres://prod/orders --config ./pg.toml
mfs search "refunds stuck in pending over 7 days" postgres://prod/orders
```

<details>
<summary>Output</summary>

```text
postgres://prod/orders  score=0.79
  {"id":"ord_8842","status":"pending","refund_requested_at":"2026-05-30",
   "amount":129.00,"gateway":"stripe","note":"customer disputed, awaiting ..."}
```

</details>

### 🌐 …or one query across all of them at once

Register a few sources, then `--all` fans a single query across every one of
them — local files, databases, ticket trackers, chat — and returns one stable
result shape, so any hit copies straight into `mfs cat` for the exact evidence.

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

## 🔌 Connectors

Local files are only the start. MFS speaks to a growing catalog of sources —
object stores, databases, code hosts, issue trackers, CRMs, chat, mail, docs —
and mounts each one as a **URI tree** you `ls` / `cat` / `grep` / `search` like a
local directory. Same verbs, same result shape, everywhere.

| Category | Source | Scheme | What you search |
|---|---|---|---|
| 📁 Files & objects | Local files | `file://` | any folder — text, Markdown, code, PDF, docx, images |
| | Amazon S3 (& R2 / GCS / MinIO) | `s3://` | bucket objects, converted to text |
| | Google Drive | `gdrive://` | Docs, Sheets, PDFs and files in a Drive |
| 🗄️ Databases | PostgreSQL | `postgres://` | tables and rows as searchable records |
| | MySQL | `mysql://` | tables and rows as searchable records |
| | MongoDB | `mongo://` | collections and documents |
| | BigQuery | `bigquery://` | datasets and tables |
| | Snowflake | `snowflake://` | databases and tables |
| 💻 Code & issues | GitHub | `github://` | repo files, issues, and PRs |
| | Jira | `jira://` | projects, issues, comments |
| | Linear | `linear://` | teams, issues, comments |
| 🧑‍💼 CRM & support | HubSpot | `hubspot://` | contacts, companies, deals, notes |
| | Zendesk | `zendesk://` | support tickets and comments |
| 💬 Chat & mail | Slack | `slack://` | channels and message history |
| | Discord | `discord://` | servers, channels, threads |
| | Gmail | `gmail://` | mail threads and messages |
| | Feishu / Lark | `feishu://` | docs and messages |
| 🌐 Docs & web | Notion | `notion://` | pages and databases |
| | Web | `web://` | crawled pages, converted to Markdown |

Every connector reads the same way once registered — register + index, browse,
then search, all with the verbs you already know:

```bash
mfs add    github://your-org/your-repo --config ./github.toml   # register + index
mfs ls     github://your-org/your-repo                          # browse the tree
mfs search "flaky retry logic" github://your-org/your-repo      # scoped search
```

Not sure a source will connect? Probe it first — no registration, no writes:

```bash
mfs connector probe linear://workspace --config ./linear.toml
```

New connectors slot in behind the same interface, so the catalog keeps growing
without changing how you use it. Each connector has its own credential setup and
TOML shape.

## 🏗️ Architecture

The `mfs` CLI is a thin Rust client; the **server** holds all the heavy state,
secrets, and workers. The same build runs two ways — both on **one machine**
(the quick-start path above) or **split** for production: the server in your
data center / VPC / k8s cluster, the CLI and SDKs wherever your developers and
agents are.

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

**Four client surfaces** reach the server over the same HTTP `/v1` API, so the
expensive work always stays in one place:

| On the client | On the server |
|---|---|
| `mfs` **CLI** (Rust, 2–4 ms cold start, ~6 MB binary) | Connector credentials, env vars, TOML config |
| Generated **SDKs** (Python, TypeScript) | Queue + workers, indexing jobs |
| Agent **skill packs** (`mfs-find`, `mfs-ingest`) | Metadata DB (SQLite or Postgres) |
| **HTTP `/v1`** (OpenAPI) for anything custom | Vector index (Milvus Lite, self-hosted Milvus, or Zilliz Cloud) |
| Endpoint / token resolution + output rendering | Caches + model work (embedding, VLM, summary, chunking, conversion) |

The client carries no persistent state, so re-creating it on a new laptop, a CI
runner, or inside an agent runtime is free — everything that matters lives on
the server.

**Going split (production).** Configure the server once
([below](#configure-the-server)), then point the CLI at it:

```bash
export MFS_API_URL=https://mfs.your-corp.internal
export MFS_API_TOKEN=...
mfs status
```

Docker images, a Compose file, and a Helm chart for split api / worker
deployments live under [`deployments/`](deployments/).

## ⚙️ Configure the server

Almost all configuration lives **on the server** — the embedding provider, the
vector backend, the metadata database, caches, auth, and every connector's
credentials all sit in the server's `server.toml`. The **client** barely has any
config: a tiny TOML that just says *which server to talk to* (endpoint + token),
so pointing the CLI at a local or a remote server is the whole client story.

There are two ways to configure the server, and they write the same `server.toml`:

- **The wizard** — `mfs-server setup` is a convenient interactive walk through
  the common choices (embedding provider, vector DB, database, cache, auth).
  Defaults are self-contained, so you can press Enter through to a working local
  server and opt into hosted backends (OpenAI, Zilliz Cloud, Postgres, …) only
  where you need them.
- **The TOML** — everything the wizard writes, plus the advanced knobs it
  doesn't surface (cache size / eviction, chunker thresholds, namespace, worker
  counts, per-connector tuning), lives in `~/.mfs/server.toml`. Edit it directly
  for anything beyond the basics.

<p align="center">
  <img src="https://github.com/user-attachments/assets/2adc8090-76e2-4073-aae8-776ca4ba541e" alt="mfs-server setup wizard demo" width="820" />
</p>

```bash
uv run mfs-server setup                      # full interactive walkthrough
uv run mfs-server setup --section embedding  # re-run just one section
```

## ✨ Features & how it works

- **🗂️ One file-like interface over everything** — `ls` · `cat` · `tree` ·
  `grep` · `head` · `tail` · `search`, across local files and every connector,
  with one stable result shape.
- **🔍 Hybrid search you can trust** — semantic + keyword retrieval, and every
  hit reopens to the exact bytes or rows via its locator. Never trust a hit blind.
- **🏠 Local-first, no cloud needed** — default ONNX embeddings + Milvus Lite +
  SQLite run fully offline; no API key, no GPU. Swap in OpenAI / Zilliz Cloud /
  Postgres when you want.
- **🔌 A growing connector catalog** — files, object stores, databases, code,
  issues, CRM, chat, mail, docs — all behind the same verbs.
- **📄 Local conversion, any format** — PDF / docx → Markdown locally; an
  optional vision model makes images searchable too.
- **🧱 Thin client, stateful server** — a few-MB Rust CLI plus SDKs / skills /
  HTTP `/v1`; run both on a laptop or split for production.
- **🛡️ Robust by design** — the index is derived state: crash-safe and
  rebuildable, content-addressable caching, idempotent `DELETE + INSERT` writes,
  three-tier rename detection (rename ≠ re-embed), and a three-layer ignore.
  Recovery is just *rerun `mfs add`*.

Three principles run underneath all of it:

- **Upstream is the source of truth** — MFS keeps a derived index; delete
  `~/.mfs/` and you lose no data, `mfs add` rebuilds from the original sources.
- **Search × browse, two legs of one loop** — like a library: point at the
  shelf, flip pages, read the right one. Never trust a hit until you've reopened it.
- **File-like URIs because agents already speak shell** — no new query language,
  no per-source SDK; the same handful of verbs cover every connector.

## 🛠️ Build agents on top of MFS

If you're building an agent project (not just calling MFS from a shell), MFS
becomes the harness — the retrieval and context layer your agent sits on top of,
not a passive index it queries occasionally.

A modern agent project juggles several streams of state at once:

- **Memory** — past sessions, recaps, decision logs, scratch notes
- **Skills** — reusable `SKILL.md` packs, prompts, runbooks
- **Code** — every repo the agent reads or writes
- **Knowledge** — docs, PDFs, design specs, meeting transcripts
- **Work signals** — Slack threads, emails, tickets, CRM records, database
  state, dashboards

Without a harness this spreads across local folders, SaaS apps, and private
databases. With MFS the agent gets one CLI surface over all of it — and you skip
writing a connector per source.

Three ways to wire MFS into your agent:

- **🧩 Skill packs.** Drop [`skills/mfs-find`](skills/mfs-find/SKILL.md) and
  [`skills/mfs-ingest`](skills/mfs-ingest/SKILL.md) into your coding agent
  runtime, and the agent inherits the whole search-and-browse loop with no
  custom integration code.
- **📦 SDKs.** Generated Python and TypeScript clients under `sdks/` cover the
  cases where shelling out to `mfs` is awkward (long-running daemons, language
  runtimes without a shell).
- **🔗 HTTP `/v1`.** Skills and SDKs are thin wrappers around the same OpenAPI
  surface — go direct when you need to.

## 🗺️ Roadmap

- Publish `mfs-server` to PyPI for one-command installs.
- OAuth `client_credentials` for Salesforce and OAuth-only orgs.
- More connectors (Confluence, Asana, Drive shared drives).
- Lock `/v1` HTTP API for the `v0.4.0` final.

## 🚦 Status

`v0.4.0-beta.2`. The CLI surface and connector catalog are stable;
the HTTP API may still shift before `v0.4.0` final, so pin versions
in scripts. Found a bug? Open an issue:
<https://github.com/zilliztech/mfs/issues>.

## 🙏 Acknowledgements

MFS is shaped by several related projects:

- [claude-context](https://github.com/zilliztech/claude-context) and
  [memsearch](https://github.com/zilliztech/memsearch) — earlier
  Zilliz code-search and memory-search efforts whose community
  feedback shaped MFS's agent-facing direction.
- [VKFS](https://github.com/ZeroZ-lab/vkfs) — a sister exploration
  of a Unix-like interface for agent access to vector-backed
  knowledge.

## ⚖️ License

Apache-2.0. See [LICENSE](LICENSE).
