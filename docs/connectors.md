# Connectors

A connector turns a source into a file-like URI tree. Once you register one, the
same commands you use on a local folder work everywhere — search, grep, ls, tree,
cat — whether the bytes live in a GitHub repo, a Postgres table, a Slack channel,
or an S3 bucket:

```bash
mfs search "release checklist" github://owner/repo
mfs ls slack://acme/channels
mfs cat postgres://prod-db/public/tickets/rows.jsonl --locator '{"id": 12345}'
```

This page covers what's available and the mechanics shared across all
connectors — the lifecycle, the credential model, and the `[[objects]]` config
for structured sources. Each connector has its own page with its URI shape,
credentials, and quirks; the catalog below links to them.

!!! note "Connectors are optional extras"
    `file` is always available. The other connectors load lazily and are skipped
    on a server that doesn't have their dependencies installed. A scheme being
    listed here means it's in the codebase — not that it's installed on every
    server. Always `probe` on the target server before a large sync.

## The catalog

**Local & web**

| Connector | Use it for |
|---|---|
| [`file`](connectors/file.md) | Local directory trees — code, docs, PDFs. No credentials. |
| [`web`](connectors/web.md) | Crawl HTTP(S) pages into searchable markdown. |
| [`s3`](connectors/s3.md) | Objects under an S3 / R2 / GCS / MinIO bucket prefix. |
| [`gdrive`](connectors/gdrive.md) | Google Drive files visible to a user OAuth token. |
| [`notion`](connectors/notion.md) | Notion pages and database records shared with an integration. |

**Databases & warehouses**

| Connector | Use it for |
|---|---|
| [`postgres`](connectors/postgres.md) | Postgres table rows + schema, incremental sync. |
| [`mysql`](connectors/mysql.md) | MySQL table rows + schema in one database. |
| [`mongo`](connectors/mongo.md) | MongoDB documents, one record each. |
| [`bigquery`](connectors/bigquery.md) | BigQuery table rows via Application Default Credentials. |
| [`snowflake`](connectors/snowflake.md) | Snowflake table rows, key-pair / password / PAT auth. |

**Issues, code & work tracking**

| Connector | Use it for |
|---|---|
| [`github`](connectors/github.md) | Repo code, plus optional issues and pull requests. |
| [`jira`](connectors/jira.md) | Jira issues by project (Cloud or Server). |
| [`linear`](connectors/linear.md) | Linear issues across teams. |

**CRM & support**

| Connector | Use it for |
|---|---|
| [`hubspot`](connectors/hubspot.md) | HubSpot CRM objects — contacts, companies, deals, tickets. |
| [`zendesk`](connectors/zendesk.md) | Zendesk tickets, comments, users, organizations. |

**Chat, mail & collaborative docs**

| Connector | Use it for |
|---|---|
| [`slack`](connectors/slack.md) | Slack channel threads and the user directory. |
| [`discord`](connectors/discord.md) | Discord guild channel messages and active threads. |
| [`gmail`](connectors/gmail.md) | Gmail threads under chosen labels. |
| [`feishu`](connectors/feishu.md) | Feishu / Lark chats and docx documents. |

## File-like vs structured sources

Connectors come in two shapes, and the difference decides how much config you
write:

- **File-like** sources (`file`, `web`, `github` code, `s3`, `gdrive`, `notion`
  pages) expose real files. They're classified by type and indexed automatically —
  no per-object config needed.
- **Structured** sources (the databases, plus issue/CRM/chat connectors) expose
  rows, records, and message threads as `.jsonl` objects. To make a row
  searchable, MFS needs to know which fields carry text. The public SaaS
  connectors ship **built-in presets** so they work out of the box; the database
  connectors need an `[[objects]]` block per table (see below).

## Lifecycle

```bash
mfs connector probe <target> --config ./connector.toml   # check creds + reachability
mfs add <target> --config ./connector.toml               # register + index (returns a job)
mfs job show JOB_ID                                       # watch it reach succeeded
```

| Task | Command |
|---|---|
| Probe without registering | `mfs connector probe <target> --config <file>` |
| Add / register and index | `mfs add <target> --config <file>` |
| Skip the confirm prompt | `mfs add <target> --config <file> --yes` |
| Re-sync (incremental) | `mfs add <target>` again |
| Re-sync from a date | `mfs add <target> --since <date>` (only [`gdrive`](connectors/gdrive.md) and [`feishu`](connectors/feishu.md)) |
| Force a full re-index | `mfs add <target> --force-index` |
| List connectors | `mfs connector list` |
| Inspect one | `mfs connector inspect <target>` |
| Update config | `mfs connector update <target> --config <file>` |
| Remove | `mfs connector remove <target>` (or `mfs remove <target>`) |

For an external target, `mfs add` first runs a **zero-billing estimate** — it
discovers the object count and dry-runs the chunker/tokenizer (no embedding API
calls) and asks you to confirm before queueing. Pass `--yes` to skip it.

!!! warning "Remove is destructive inside MFS"
    Removing a connector never touches the source system, but it does drop the
    connector's MFS metadata, artifacts, and index. Keep the confirmation prompt
    on unless you're scripting deliberately.

## Credentials: references, not values

A connector's TOML never holds a raw secret. It carries a **reference**, and the
server resolves it when it builds the plugin — so the real value lives on the
server, never in the TOML or the database:

```toml
token = "env:SLACK_BOT_TOKEN"                       # from a server env var
dsn = "env:PG_PROD_DSN"
credential_ref = "file:/etc/mfs/snowflake/rsa_key.p8"   # file contents (k8s/docker secret)
```

- `env:VAR_NAME` — read from the **server process** environment (not your client
  shell) when the connector is constructed.
- `file:/abs/path` — the file's contents; the path must be absolute.

Set env vars in the environment that starts `mfs-server`, and keep credential
files outside the repo with tight permissions. See
[Auth and secrets](auth-and-secrets.md#connector-credentials) for the full
boundary.

## `[[objects]]` for structured rows

Database connectors need an object rule per table so MFS knows which columns are
searchable text and which key reopens an exact record:

```toml
[[objects]]
match = "/public/tickets"               # connector-relative object path
text_fields = ["title", "description"]  # embedded, searchable content
locator_fields = ["id"]                 # the key for `cat --locator`
metadata_fields = ["status", "updated_at"]  # returned alongside hits
```

The public SaaS connectors (GitHub issues, Jira, Slack, Linear, Zendesk tickets,
Gmail, Discord, Feishu) ship presets, so `[[objects]]` is optional there — use it
only to override the default fields. See [Content model](content-model.md) for how
these become `content`, `locator`, and `metadata.fields` in a result.

## After ingest

Every scheme answers the same commands:

```bash
mfs search "billing escalation" zendesk://acme --top-k 10
mfs search "rate limit retry policy" --all
mfs tree postgres://prod-db -L 3
mfs cat github://owner/repo/src/main.rs --range 100:150
```

See [Search and browse](search-and-browse.md) for the retrieval loop.
