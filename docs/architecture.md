# Architecture

MFS is a **thin client over a stateful server**, talking over one HTTP `/v1` API.
Everything runs the simplest way — client and server on one machine — and the
same design scales to production by moving where the server runs. The split is
deliberate, and it decides where everything lives.

- **Client** — the `mfs` CLI, the SDKs, and the agent skills (`mfs-find` /
  `mfs-ingest`). Stateless, so re-creating it on a laptop, a CI runner, or an
  agent runtime is free.
- **Server** (`mfs-server`) — the setup wizard, all config / credentials / env
  vars, the queue and workers, the engine and connectors, and the data backends.
  Everything that matters lives here, so `env:` / `file:` secret references always
  resolve on the server, never on the client.

```text
┌────────────────┐                 ┌────────────────────────────────────────┐
│ CLIENT         │                 │ SERVER · mfs-server                    │
│ ────────────── │                 │ ────────────────────────────────────── │
│ mfs CLI        │                 │ setup wizard                           │
│ SDKs           │                 │ queue + workers                        │
│ skills         │                 │ config · env vars · credentials        │
│   · mfs-find   │ ── HTTP /v1 ──▶ │ engine · connectors · processors       │
│   · mfs-ingest │                 │ backends (scale up as needed):         │
└────────────────┘                 │   vector    Milvus Lite → Zilliz Cloud │
                                   │   metadata  SQLite → Postgres          │
                                   │   caches    local filesystem → S3      │
                                   └────────────────────────────────────────┘
```

The CLI's only job is to parse a command, resolve the endpoint and token, package
an upload when one is needed, call `/v1`, and render the result. Everything
stateful — what's registered, what's indexed, what's cached — belongs to the
server.

## Core concepts

A handful of words turn up all over these docs and the diagrams. Here they are in
plain terms, with one running example: you've just run `mfs add ./repo`.

| Term | What it is | In the example |
|---|---|---|
| **Connector** | A registered source. One connector holds **many** objects. | `./repo`, a local folder. (Others: `postgres://prod`, `slack://eng`.) |
| **Object** | One virtual "file" under a connector — really just one URI path. | each file in the repo, like `src/main.py`. (For a database, an object is a table's `rows.jsonl`.) |
| **ConnectorJob** | One sync of a *whole* connector — connector-level — with a status you can watch. | the single indexing run you just started for `./repo`. |
| **ObjectTask** | The work for *one* object inside a job — object-level: convert it, split it, embed it. | "process `src/main.py`" is one task; the job spawns thousands, one per file. |
| **Queue** | Where ObjectTasks wait for a worker. There are two: a durable, DB-backed queue for the per-object work, and a fast in-memory queue (the ChunkQueue) further down the pipeline — see [Ingest pipeline](ingest-pipeline.md). | the per-file tasks line up here until a worker claims one. |
| **Metadata DB** | The bookkeeping — which connectors, objects, and ConnectorJobs exist and their state. The durable queue lives here too. | tracks the run and its per-file tasks. |
| **Cache** | Derived bytes kept so MFS never redoes heavy work for the same input. Two kinds: the **artifact cache** holds conversions (a PDF turned into Markdown), the **transformation cache** holds model inputs and outputs (an embedding already computed). | a converted PDF reused on the next sync; an embedding not paid for twice. |
| **Index** | The searchable rows, in Milvus — what `search` and `grep` actually hit. | the repo, now findable by meaning. |

The level is the thing to keep straight: a **connector** holds **many objects**
(one per URI path); a **ConnectorJob** runs at the connector level — one sync of
the whole source — and fans out into one **ObjectTask** per object. So
`mfs add ./repo` is a single ConnectorJob that spawns thousands of ObjectTasks,
one per file.

And the source is always the truth — everything in the Cache and the Index is
derived from it, and can be thrown away and rebuilt.

## Where each piece runs

The only real deployment choice is **where the server runs**. Run it on your own
machine, move it onto its own host (a VM or a single container), or scale it out
across Compose or Kubernetes — the CLI and skills stay with you either way. This
is the recommended layout per mode; for how to set each piece, see
[Configuration](configuration.md), and for the topologies see
[Deployment](deployment.md).

| Piece | Local (one machine) | Single host (its own VM or container) | Distributed (Compose / Kubernetes) |
|---|---|---|---|
| `mfs` CLI | your machine | your machine | your machine |
| Agent skills | your machine | your machine | your machine |
| `mfs-server` + workers | your machine | the server host | the server cluster (api + worker pods) |
| `mfs-server setup` wizard | your machine | the server host | the server cluster |
| `server.toml` | your machine | the server host | the server cluster (ConfigMap / mounted file) |
| Connector credentials + secret files | your machine | the server host | the server cluster (Docker / k8s secrets) |
| `env:` / `file:` ref values | your machine | the server host | the server cluster (pod env / mounted files) |
| Vector DB | Milvus Lite (local file) | self-hosted Milvus or Zilliz Cloud | Zilliz Cloud |
| Metadata DB | SQLite (local file) | Postgres | Postgres |
| `file://` ingest | server reads the path in place | CLI bundles + uploads the tree | CLI bundles + uploads the tree |

A few rows are worth spelling out:

- **The client never holds state worth backing up.** The CLI and skills are
  always "your machine"; the only client file is `client.toml` (which server to
  talk to). Everything stateful is server-side, so a laptop, CI runner, or fresh
  container reconnects with zero setup.
- **Backends scale by configuration, not by code.** Locally everything sits in
  `$MFS_HOME` (default `~/.mfs`): `server.toml`, the generated token, SQLite,
  the artifact cache, the ONNX model cache, and Milvus Lite. Point the vector
  backend at Zilliz Cloud and metadata at Postgres and the same server runs at
  scale — the engine and connectors don't change.
- **`file://` ingest is automatic.** On a shared filesystem the server reads the
  path directly; otherwise the CLI bundles and uploads it — no flag needed. An
  agent never has to think about this.

For a split deployment, point the CLI at the server and you're set:

```bash
export MFS_API_URL=https://mfs.your-corp.internal
export MFS_API_TOKEN=...
mfs status
```

## Where credentials live

A connector's TOML never holds a raw secret — it carries a **reference**, and the
server resolves it when it builds the connector:

- `env:VAR_NAME` — read from the **server process** environment.
- `file:/abs/path` — the contents of a file the **server** can read (a mounted
  Docker / k8s secret, a PEM key).

Because resolution happens on the server, the CLI and your agent never touch raw
credentials, and the database keeps only the `env:` / `file:` reference, never the
value. Set the variables where `mfs-server` runs — your machine in local mode, the
server host or pod otherwise. See [Auth and secrets](auth-and-secrets.md) for the
full boundary and [Design philosophy](production.md) for *why* it's built this
way.

## File is the special case

Every source is a connector, and they're all the same kind of thing: `postgres`,
`slack`, `github`, and `file` each implement the same `list / stat / read /
fingerprint / sync` contract, flow through the same pipeline, and get the same
search. The one real exception is `file` — and the reason is *where the bytes
are*. The server reaches most sources itself: it connects to Postgres, it calls
the Slack API, and pulls the data directly. Local files are different — in a
client/server setup the bytes sit on the client machine, where the server can't
see them, so the file connector adds an upload step (manifest diff → upload →
commit). That special case stays isolated: the file connector's sync logic is the
same whether it runs local or remote; only how the bytes reach the server
changes.
