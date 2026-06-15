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

## 📥 Install the agent skills

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

## ⚡ Quick start

**No API key, no GPU, no cloud account** — defaults are local ONNX embeddings +
Milvus Lite + SQLite under `~/.mfs/`.

Start a local server (from source until it's published):

```bash
git clone https://github.com/zilliztech/mfs.git
cd mfs/server/python && uv sync && uv run mfs-server run
```

Then install the skills (above) and just ask your agent — on first run the
`mfs-ingest` skill pre-flights and installs the `mfs` CLI for you, so there's
little left to set up by hand:

```text
/mfs-ingest index ~/notes
/mfs-find what did I decide about the pricing model?
```

The agent indexes the folder, runs the search, opens the top hit, and quotes the
exact lines back with the file path — so you can trust the answer instead of a
paraphrase. (The first search pulls a ~600 MB local model into `~/.mfs/`, then
the stack runs offline.)

<details>
<summary>Prefer the shell, no agent?</summary>

Install the CLI (a small Rust binary), then run the same loop directly:

```bash
curl --proto '=https' --tlsv1.2 -LsSf \
  https://github.com/zilliztech/mfs/releases/download/v0.4.0-beta.2/mfs-cli-installer.sh | sh

mfs add ~/notes
mfs search "the pricing model decision" ~/notes --top-k 5
```

> macOS: run `xattr -d com.apple.quarantine $(which mfs)` once if it prompts
> about an unidentified developer.

</details>

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

## 💡 Use cases

Every source rides the same **ingest → search → read** loop. The outputs below
are illustrative — expand each to see the result shape.

### 🌐 One query across everything

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
mfs search "audit-log retention and the dashboards that show it"
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


## 🏗️ Architecture: thin client, stateful server

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
([below](#configure-the-server-wizard-or-toml)), then point the CLI at it:

```bash
export MFS_API_URL=https://mfs.your-corp.internal
export MFS_API_TOKEN=...
mfs status
```

Docker images, a Compose file, and a Helm chart for split api / worker
deployments live under [`deployments/`](deployments/); see
[docs/deployment.md](docs/deployment.md) for the walkthrough.

## ⚙️ Configure the server: wizard or TOML

The interactive `mfs-server setup` wizard walks seven sections. The defaults
are self-contained, so you can press Enter through to a working local server
and opt into hosted backends only where you need them.

<p align="center">
  <img src="https://github.com/user-attachments/assets/2adc8090-76e2-4073-aae8-776ca4ba541e" alt="mfs-server setup wizard demo" width="820" />
</p>

Run a single section any time:

```bash
uv run mfs-server setup --section embedding
```

For advanced knobs (cache size, eviction policy, chunker thresholds,
namespace, custom worker count) — edit `~/.mfs/server.toml`
directly. See [docs/configuration.md](docs/configuration.md) for the
full field reference.

## 🔌 Connectors

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

## 📚 Docs

The full guide lives in **[docs/](docs/)** (also served via MkDocs):

- [Quickstart](docs/getting-started.md) — first local run, end to end.
- [Search and Browse](docs/search-and-browse.md) — the search →
  locate → read loop.
- [Connectors](docs/connectors.md) — catalog and per-connector setup.
- [Configuration](docs/configuration.md) — server settings, env vars,
  auth.
- [Deployment](docs/deployment.md) — Docker, Compose, remote server.
- [Troubleshooting](docs/troubleshooting.md) — when things break.

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
