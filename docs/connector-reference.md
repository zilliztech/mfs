# Connector Reference

This page keeps connector-specific details searchable inside the MkDocs site.
Use [Connectors](connectors.md) for choosing a source and managing its
lifecycle. Use [Connector Configuration Recipes](connector-config-recipes.md)
when you already chose a scheme and need to author, probe, update, and validate
TOML. Use this page when you need a concrete URI shape, minimum TOML,
probe/add command, browse path, locator, or pitfall for one built-in scheme.

The built-in scheme list below is taken from the server registry. `file` is
always imported. The other built-ins are imported lazily and may be skipped in a
server environment that does not have that connector's optional dependencies.
Always probe the connector on the target server before queueing a large sync.

```bash
mfs connector probe <scheme://alias> --config ./connector.toml
mfs add <scheme://alias> --config ./connector.toml
mfs job show JOB_ID
```

`mfs add` returns a job id immediately. Wait until that job reaches `succeeded`
before relying on the search or browse examples below.

!!! note "Config and secret references"
    Connector TOML is loaded by the CLI and sent as the API `config` object.
    Top-level `env:VAR` and `file:/abs/path` string values are resolved by the
    server before the plugin is built. Use `credential_ref` when a connector
    explicitly consumes the resolved `credential` fallback, such as Snowflake's
    private key.

For a cross-scheme decision table, credential matrix, `[[objects]]` field
guide, recipe tabs, and readback validation examples, see
[Connector Configuration Recipes](connector-config-recipes.md).

## Built-in Scheme Index

| Scheme | Primary tree shape | Use it for |
|---|---|---|
| [`file`](#file) | `file://local/abs/path/...` | Local directory trees and uploaded local paths. |
| [`web`](#web) | `web://alias/pages/<host>/<path>.md` | HTTP pages converted to markdown. |
| [`github`](#github) | `github://owner/repo/<repo-path>` | GitHub repository files, optionally issues and PRs. |
| [`postgres`](#postgres) | `postgres://alias/<schema>/<table>/rows.jsonl` | Postgres rows and table schemas. |
| [`mysql`](#mysql) | `mysql://alias/<table>/rows.jsonl` | MySQL rows in one database. |
| [`mongo`](#mongo) | `mongo://alias/<collection>/documents.jsonl` | MongoDB documents in one database. |
| [`slack`](#slack) | `slack://alias/channels/<name>__<id>/messages.jsonl` | Slack channel threads and workspace users. |
| [`discord`](#discord) | `discord://alias/channels/<name>__<id>/messages.jsonl` | Discord text channel messages and active threads. |
| [`gmail`](#gmail) | `gmail://alias/labels/<label>__<id>/messages.jsonl` | Gmail label streams grouped by `threadId`. |
| [`notion`](#notion) | `notion://alias/pages/<id>.md` | Notion pages and data source records. |
| [`jira`](#jira) | `jira://alias/projects/<key>/issues.jsonl` | Jira issue records by project. |
| [`linear`](#linear) | `linear://alias/teams/<key>/issues.jsonl` | Linear issue records by team. |
| [`zendesk`](#zendesk) | `zendesk://alias/tickets/records.jsonl` | Zendesk tickets, comments, users, and organizations. |
| [`hubspot`](#hubspot) | `hubspot://alias/<object>/records.jsonl` | HubSpot CRM object records. |
| [`bigquery`](#bigquery) | `bigquery://alias/<dataset>/tables/<table>/rows.jsonl` | BigQuery table rows and schemas. |
| [`snowflake`](#snowflake) | `snowflake://alias/<DB>/<SCHEMA>/tables/<TABLE>/rows.jsonl` | Snowflake table rows and schemas. |
| [`s3`](#s3) | `s3://alias/<object-key>` | S3-compatible object-key trees. |
| [`gdrive`](#gdrive) | `gdrive://alias/<folder>/<file>` | Google Drive file trees. |
| [`feishu`](#feishu) | `feishu://alias/chats/<name>__<id>/messages.jsonl` | Feishu/Lark chats and docx documents. |

## `file`

**URI shape:** bare local paths are accepted by the CLI. The server identity for
same-host paths is `file://local/abs/path`. Uploaded remote paths use
`file://<client_id><abs-root>`.

**Minimum config:** no TOML for the simple case. Optional TOML can set
`max_file_bytes` and `[[objects]]` rules such as `indexable = false`.

**Start:**

```bash
mfs add ./docs
```

**Search or browse:**

```bash
mfs search "release checklist" ./docs --top-k 10
mfs cat ./docs/README.md --range 1:80
```

**Common pitfalls:**

- `.gitignore`, `.mfsignore`, and built-in ignore patterns remove files from
  the visible tree.
- Large files can be skipped by `max_file_bytes`.
- `indexable = false` keeps an object browsable but prevents embedding chunks.
- Symlinks are resolved under the connector root; paths that escape the root
  are rejected.

## `web`

**URI shape:** `web://<alias>/pages/<host>/<url-path>.md`. URLs are fetched,
converted to markdown, and exposed under `pages/`.

**Minimum config:**

```toml
start_urls = ["https://docs.example.com/"]
allowed_domains = ["docs.example.com"]
max_pages = 100
```

**Start:**

```bash
mfs connector probe web://docs --config ./web.toml
mfs add web://docs --config ./web.toml
```

**Search or browse:**

```bash
mfs search "installation" web://docs
mfs ls web://docs/pages/docs.example.com
mfs cat web://docs/pages/docs.example.com/index.md --range 1:80
```

**Common pitfalls:**

- The connector fetches HTTP responses; it does not execute client-side
  JavaScript.
- `allowed_domains` limits traversal. External links may appear in markdown but
  are not indexed unless they match the allowed domain set.
- `max_pages` is a crawl cap; raise it and re-sync if pages are missing.
- Authentication is not modeled by the current plugin.

## `github`

**URI shape:** `github://<owner>/<repo>/...`. Repository files are exposed at
their repo paths, for example `github://zilliztech/mfs/server/python/...`.
When `index_meta = true`, `_meta/issues.jsonl`, `_meta/pulls.jsonl`, and PR
diff documents are also exposed.

**Obtain credentials:** you need a **GitHub Personal Access Token (PAT)** with
read scopes. Fine-grained PAT is the recommended path for org-owned repos.

Fine-grained PAT:

1. Open <https://github.com/settings/tokens?type=beta> → **Generate new
   token**. Pick a name and expiration (90-day default; set a reminder to
   rotate).

   ![GitHub personal access tokens page](https://github.com/user-attachments/assets/d7fee707-304f-4195-9e45-de1577493558)

   ![GitHub generate token menu](https://github.com/user-attachments/assets/c837450e-48ed-43ef-893a-7828f2741bc5)

   ![GitHub fine-grained token form](https://github.com/user-attachments/assets/62d9923f-52bb-4e2a-a9f8-b9707bc48bc4)

2. Repository access: **Only select repositories** → pick the ones to index.

   ![GitHub repository access options](https://github.com/user-attachments/assets/d901ba9a-b8cb-4a39-9a20-27eb6668cafa)

3. Repository permissions:
    - **Contents** → Read-only
    - **Issues** → Read-only
    - **Pull requests** → Read-only
    - **Metadata** → Read-only (always required)

   ![GitHub repository permissions](https://github.com/user-attachments/assets/a887d024-26b1-418e-b063-24def34ccde9)

4. Generate the token and copy the `github_pat_...` value. That goes into
   `GITHUB_TOKEN`.

Classic PAT works too (<https://github.com/settings/tokens/new>, scope
`repo` for private or `public_repo` for public-only). If the target org
enforces SSO, click **Configure SSO** on the generated token and authorize
it for the org.

**Minimum config:**

```toml
repo = "zilliztech/mfs"
branch = "main"
token = "env:GITHUB_TOKEN"
index_meta = true
max_read_rows = 5000
```

The plugin reads the `token` config field; author it as an `env:` reference
(e.g. `token = "env:GITHUB_TOKEN"`) so the secret stays in the server
environment, never in the TOML. `file:/abs/path` works too.

**Start:**

```bash
mfs connector probe github://zilliztech/mfs --config ./github.toml
mfs add github://zilliztech/mfs --config ./github.toml
```

**Search or browse:**

```bash
mfs search "connector registry" github://zilliztech/mfs/server/python
mfs cat github://zilliztech/mfs/server/python/src/mfs_server/connectors/registry.py --range 1:80
mfs cat github://zilliztech/mfs/_meta/issues.jsonl --locator '{"number":42}'
```

**Common pitfalls:**

- Set `repo` explicitly in TOML. The current plugin reads `repo` from config
  instead of deriving it from the URI.
- Issues, pulls, and PR diffs are opt-in through `index_meta = true`.
- Private repositories need a `token` (e.g. `token = "env:GITHUB_TOKEN"`).
- Submodules are not followed as separate repository trees.

## `postgres`

**URI shape:** `postgres://<alias>/<schema>/<table>/rows.jsonl` for rows and
`postgres://<alias>/<schema>/<table>/schema.json` for schema summaries.

**Obtain credentials:** you already have a database; what you need is a DSN
in the form `postgresql://user:pass@host:5432/dbname` and a role with
`SELECT` on the schemas you want indexed.

- Cloud Postgres (AWS RDS / Aurora, GCP Cloud SQL, Azure): copy the
  connection string from the cloud console. Replace any `{{password}}`
  placeholder with the real password (or use IAM auth — it still resolves
  to a DSN).
- Self-hosted: run `\conninfo` inside `psql` to see host / port / database /
  user, then compose the DSN.
- Docker compose: the same DSN your other services use works here.

Confirm connectivity from the same machine that will run mfs-server before
handing the DSN to MFS — if `psql` can't see the tables, neither can the
connector:

```bash
psql "$DSN" -c "SELECT 1"
psql "$DSN" -c "\\dt"
```

Use a read-only role. The minimum grant is `USAGE` on each in-scope schema
plus `SELECT` on its tables.

**Minimum config:**

```toml
dsn = "env:PG_DSN"
schemas = ["public"]
max_read_rows = 100000

[[objects]]
match = "/public/tickets"
text_fields = ["title", "description"]
locator_fields = ["id"]
metadata_fields = ["status", "updated_at"]
```

**Start:**

```bash
mfs connector probe postgres://prod-db --config ./postgres.toml
mfs add postgres://prod-db --config ./postgres.toml
```

**Search or browse:**

```bash
mfs search "SSO migration" postgres://prod-db/public/tickets/rows.jsonl
mfs search "email column" postgres://prod-db --kind schema_summary
mfs cat postgres://prod-db/public/tickets/rows.jsonl --locator '{"id":12345}'
```

**Common pitfalls:**

- Missing `[[objects]].text_fields` means rows enumerate but produce no
  searchable row chunks.
- `match` should target the connector-relative object path, such as
  `/public/tickets`, so it matches `/public/tickets/rows.jsonl`.
- Use read-only database credentials; the connector only needs `SELECT`.
- `max_read_rows` caps large tables and can mark recall as partial.

## `mysql`

**URI shape:** `mysql://<alias>/<table>/rows.jsonl` for rows and
`mysql://<alias>/<table>/schema.json` for schema summaries. The configured
database is the connector scope.

**Obtain credentials:** four fields — `host`, `port`, `database`, `user`,
`password`. Pull them from your existing app config / `~/.my.cnf`, or
create a dedicated read-only user:

```sql
CREATE USER 'mfs_reader'@'%' IDENTIFIED BY '<password>';
GRANT SELECT ON prod.* TO 'mfs_reader'@'%';
```

Confirm connectivity before handing the credentials to MFS:

```bash
mysql -h <host> -P <port> -u <user> -p<pw> <database> -e "SELECT 1"
mysql -h <host> -P <port> -u <user> -p<pw> <database> -e "SHOW TABLES"
```

The password goes into `MYSQL_PASSWORD` (or whichever env var you reference).

**Minimum config:**

```toml
host = "db.example.com"
port = 3306
database = "prod"
user = "mfs_reader"
password = "env:MYSQL_PASSWORD"

[[objects]]
match = "/tickets"
text_fields = ["title", "description"]
locator_fields = ["id"]
```

**Start:**

```bash
mfs connector probe mysql://prod-db --config ./mysql.toml
mfs add mysql://prod-db --config ./mysql.toml
```

**Search or browse:**

```bash
mfs search "billing bug" mysql://prod-db/tickets/rows.jsonl
mfs search "email column" mysql://prod-db --kind schema_summary
mfs cat mysql://prod-db/tickets/rows.jsonl --locator '{"id":12345}'
```

**Common pitfalls:**

- A single connector covers one database; register another connector for
  another database.
- Missing `[[objects]]` text fields gives you browsable rows but no row search.
- Legacy `utf8` collations can return mojibake for 4-byte characters.
- Long table scans can hit MySQL server timeouts; lower `max_read_rows` while
  testing.

## `mongo`

**URI shape:** `mongo://<alias>/<collection>/documents.jsonl` for documents and
`mongo://<alias>/<collection>/schema.json` for a sampled schema preview.

**Obtain credentials:** a MongoDB connection URI, in either form:

```
mongodb://user:pass@host:27017/?authSource=admin
mongodb+srv://user:pass@cluster.mongodb.net/?retryWrites=true
```

For Atlas, copy the SRV URI from **Database → Connect → Drivers** and
replace `<password>` with the real password. Use a read-only user — the
connector only needs `find()` over the in-scope collections.

Probe it before MFS sees it:

```bash
mongosh "$MONGO_URI" --eval "db.adminCommand({ping: 1})"
```

The URI goes into `MONGO_URI`.

**Minimum config:**

```toml
uri = "env:MONGO_URI"
database = "prod"
cursor_field = "updatedAt"

[[objects]]
match = "/support_threads"
text_fields = ["title", "messages[].body"]
locator_fields = ["_id"]
metadata_fields = ["status"]
```

**Start:**

```bash
mfs connector probe mongo://prod-cluster --config ./mongo.toml
mfs add mongo://prod-cluster --config ./mongo.toml
```

**Search or browse:**

```bash
mfs search "refund escalation" mongo://prod-cluster/support_threads/documents.jsonl
mfs cat mongo://prod-cluster/support_threads/documents.jsonl --locator '{"_id":"65a3..."}'
```

**Common pitfalls:**

- Mongo documents are heterogeneous; fields absent from a document are skipped
  during text rendering.
- `_id` locators use the serialized string form, not `ObjectId(...)`.
- `max_read_docs` can cap a large collection.
- The current plugin exposes `documents.jsonl`; older references may say
  `docs.jsonl`.

## `slack`

**URI shape:** `slack://<alias>/channels/<name>__<id>/messages.jsonl` for
channel messages and `slack://<alias>/users.jsonl` for workspace users.

**Obtain credentials:** you need a **Bot token** (`xoxb-...`, recommended) or
a **User token** (`xoxp-...`, when the bot can't see what the user can).

For a bot token:

1. Open <https://api.slack.com/apps> and click **Create New App** → "From
   scratch". Pick a name and the target workspace.

   ![Slack Create New App button](https://github.com/user-attachments/assets/40ffd973-84d2-483f-beca-720c723223c2)

   ![Slack Create an app dialog](https://github.com/user-attachments/assets/5119276e-cde3-405e-bd86-7fb33b2218d9)

   ![Slack From scratch app form](https://github.com/user-attachments/assets/abbdc1cf-012a-44ed-ae62-210abc252980)

2. Left sidebar → **OAuth & Permissions** → scroll to "Bot Token Scopes" and
   add:
    - `channels:read` — list public channels
    - `channels:history` — read messages in public channels
    - `users:read` — list workspace users (for `/users.jsonl`)
    - `groups:read` + `groups:history` — only if you want private channels
    - `mpim:read` + `mpim:history` — only if you want group DMs
3. Scroll back up and click **Install to Workspace** → authorize.
4. Copy the **Bot User OAuth Token** (`xoxb-...`). This is the value that
   goes into `SLACK_BOT_TOKEN`.
5. For each **private** channel you want indexed, invite the bot from
   inside the channel: `/invite @your-bot-name`. Public channels work
   without invites once the scopes are granted.

A user token (`xoxp-...`) is obtained the same way under "User Token
Scopes" instead of "Bot Token Scopes"; use it when the bot identity can't
see what you need (DMs, channels the bot wasn't invited to). It rotates
when the user revokes access.

**Minimum config:**

```toml
token = "env:SLACK_BOT_TOKEN"
channel_types = ["public_channel"]
max_read_rows = 50000
```

No `[[objects]]` is required for messages or users because built-in presets
apply `text_fields`, grouping, and locators.

**Start:**

```bash
mfs connector probe slack://acme --config ./slack.toml
mfs add slack://acme --config ./slack.toml
```

**Search or browse:**

```bash
mfs search "deploy failed" slack://acme/channels/eng-backend__C012345/messages.jsonl
mfs search "Alice Wang" slack://acme/users.jsonl
mfs cat slack://acme/channels/eng-backend__C012345/messages.jsonl --locator '{"thread_ts":"1717123456.001200"}'
```

**Common pitfalls:**

- Private channels require both scopes and bot membership.
- Search hits are thread aggregates, so a short query can reopen a long thread.
- `user` values in messages are Slack user IDs; use `/users.jsonl` to resolve
  names.
- `max_read_rows` applies per channel and can make recall partial.

## `discord`

**URI shape:** `discord://<alias>/channels/<name>__<id>/messages.jsonl` for
top-level channel messages. Active thread channels appear under
`/channels/<parent>__<id>/threads/<thread>__<id>/messages.jsonl`.

**Obtain credentials:** you need a **Bot token** and the **Guild ID** (your
server's numeric ID).

Bot token:

1. Open <https://discord.com/developers/applications> and click **New
   Application**. Name it.

   ![Discord applications page](https://github.com/user-attachments/assets/50d550fc-6e35-41e2-bbc2-deafa3aa81ca)

   ![Discord create app dialog](https://github.com/user-attachments/assets/59893fdb-b152-4f7f-83bd-8b4cf429080b)

2. Left sidebar → **Bot** → click **Add Bot** → **Reset Token** → copy the
   token. This is the value that goes into `DISCORD_BOT_TOKEN`.

   ![Discord bot token settings](https://github.com/user-attachments/assets/c6b2f7cd-0ef3-4de8-afdd-8389295f2be8)

3. On the same Bot page, scroll down to **Privileged Gateway Intents** and
   enable **Message Content Intent**. Without this the bot connects fine
   but every message comes back with empty `content`.

   ![Discord message content intent](https://github.com/user-attachments/assets/19905907-5ac7-47ed-b285-59c824660834)

4. Left sidebar → **OAuth2** → **URL Generator**:
    - Scopes: `bot`
    - Bot Permissions: `View Channels`, `Read Message History`

    ![Discord OAuth2 URL generator scopes](https://github.com/user-attachments/assets/70942093-8c13-4cd8-ab2a-39f65ea8777c)

    ![Discord bot permissions](https://github.com/user-attachments/assets/56c5ca0a-fd36-4454-94db-f7d20555a7ce)

    - Copy the generated URL, open it in a browser, and pick the server
      (guild) to add the bot to. You must be the guild owner or have
      Manage Server permission.

Guild ID:

1. In the Discord client, enable **Settings → Advanced → Developer Mode**.
2. Right-click the server name in the left sidebar → **Copy Server ID**.
   That 17–19 digit numeric string is the `guild_id` value.

**Minimum config:**

```toml
token = "env:DISCORD_BOT_TOKEN"
guild_id = "987654321098765432"
max_read_rows = 50000
```

No `[[objects]]` is required because the `discord.messages` preset applies.

**Start:**

```bash
mfs connector probe discord://community --config ./discord.toml
mfs add discord://community --config ./discord.toml
```

**Search or browse:**

```bash
mfs search "deploy failed" discord://community
mfs ls discord://community/channels/general__987654321
mfs cat discord://community/channels/general__987654321/messages.jsonl --locator '{"id":"1234567890123456789"}'
```

**Common pitfalls:**

- The bot needs Message Content Intent, or message `content` can be empty.
- Only text and announcement channels are enumerated.
- Only active threads are listed by the current plugin; archived threads are
  not included.
- Discord messages are indexed one message per row, not one thread aggregate.

## `gmail`

**URI shape:** `gmail://<alias>/labels/<label>__<id>/messages.jsonl`. Message
records are grouped by Gmail `threadId` by the framework preset.

**Obtain credentials:** Gmail uses a **user OAuth token JSON** — the
`token.json` produced by Google's OAuth flow, containing
`refresh_token` / `client_id` / `client_secret`. Service-account keys
are not supported by the current plugin.

1. Open <https://console.cloud.google.com> → create or pick a project.
2. **APIs & Services → Library → Gmail API** → click **Enable**.

   ![Google Cloud Gmail API page](https://github.com/user-attachments/assets/8a2b96ab-8ea6-4442-9e25-f186c879dd14)

3. **APIs & Services → Credentials → Create Credentials → OAuth client
   ID** → Application type: **Desktop app** → **Download JSON** (this
   is the client credentials file, not the token yet).

   ![Google Cloud Create credentials menu](https://github.com/user-attachments/assets/fc27a2aa-8eaf-4338-ada4-067be970857d)

   ![Google Cloud OAuth desktop client form](https://github.com/user-attachments/assets/2872163a-18d2-4592-ac43-b7ffc49693ed)

4. Run Google's OAuth flow once on a machine with a browser (e.g. the
   `google-auth-oauthlib` `InstalledAppFlow.run_local_server` snippet)
   requesting scope `https://www.googleapis.com/auth/gmail.readonly`.
   The flow writes a `token.json` next to the client JSON.
5. Copy `token.json` to the server and reference it from the connector
   TOML.

Required OAuth scope: `https://www.googleapis.com/auth/gmail.readonly`. The
connector only calls `messages.list` + `messages.get`; it doesn't send or
modify mail.

If you also configure the [`gdrive`](#gdrive) connector, request
`https://www.googleapis.com/auth/drive.readonly` in the same consent
step — the same `token.json` then drives both connectors.

**Minimum config:**

```toml
token = "file:/home/x/.mfs/gmail-token.json"
labels = ["INBOX"]
max_read_rows = 5000
```

The plugin builds `google.oauth2.credentials.Credentials` from the
configured `token` value when it is a string (bare access token), or from
`Credentials.from_authorized_user_info` when the parsed TOML value is an
object (inline token JSON). The most common form is the `file:`
reference above. Probe the connector in the target server before
syncing.

**Start:**

```bash
mfs connector probe gmail://work --config ./gmail.toml
mfs add gmail://work --config ./gmail.toml
```

**Search or browse:**

```bash
mfs search "contract renewal" gmail://work/labels/INBOX__CATEGORY_PERSONAL/messages.jsonl
mfs cat gmail://work/labels/INBOX__CATEGORY_PERSONAL/messages.jsonl --locator '{"threadId":"THREAD_ID","id":"MESSAGE_ID"}'
```

**Common pitfalls:**

- The current path leaf is `messages.jsonl`; older references may say
  `threads.jsonl`.
- Label matching uses Gmail label names or IDs returned by the API.
- Attachments are not indexed by the current connector.
- Large labels can hit `max_read_rows` and produce partial recall.

## `notion`

**URI shape:** pages are `notion://<alias>/pages/<page-id>.md`. Data source
records are `notion://<alias>/data_sources/<data-source-id>/records.jsonl` with
`schema.json` next to them.

**Obtain credentials:** you need a **Notion Internal Integration token**
plus explicit page-level sharing.

1. Open <https://www.notion.so/profile/integrations> → **New integration**.

   ![Notion connections page](https://github.com/user-attachments/assets/8e45d871-8304-4d4d-aab0-3a18ddba7cca)

2. Pick a name and the target workspace.

   ![Notion new connection auth method](https://github.com/user-attachments/assets/bf97bf0a-654c-43da-82d5-ac0c42ba71cc)

3. Under **Capabilities**, tick `Read content` (the other capabilities are
   not needed for MFS).

   ![Notion read content capability](https://github.com/user-attachments/assets/e0b7569f-bbf6-454a-94c1-3f74fb0923aa)

4. Submit, then copy the **Internal Integration Token** (`secret_...`).
   That goes into `NOTION_TOKEN`.

The token by itself doesn't grant access to any pages — you have to **share**
each page or database with the integration:

- Open the top-level page → top-right `•••` → **Connections** →
  **Connect to** → pick the integration.
- Sharing propagates to all sub-pages (Notion permissions are tree-rooted),
  so sharing the workspace's home page is the workspace-wide path; sharing
  just one section scopes the integration to that section.

**Minimum config:**

```toml
token = "env:NOTION_TOKEN"

[[objects]]
match = "/data_sources/<data-source-id>"
text_fields = ["Name", "Description"]
locator_fields = ["id"]
```

The `[[objects]]` block is only needed for searchable data source records.
Pages render as markdown documents without per-record config.

**Start:**

```bash
mfs connector probe notion://workspace --config ./notion.toml
mfs add notion://workspace --config ./notion.toml
```

**Search or browse:**

```bash
mfs search "launch checklist" notion://workspace/pages
mfs cat notion://workspace/pages/<page-id>.md --range 1:80
mfs cat notion://workspace/data_sources/<data-source-id>/records.jsonl --locator '{"id":"<page-id>"}'
```

**Common pitfalls:**

- The integration only sees pages and data sources that were shared with it.
- The current plugin uses Notion `data_source` APIs and paths under
  `data_sources/`; older references may say `databases/`.
- Data source records need `[[objects]].text_fields` to become searchable.
- Notion typed properties are flattened before record rendering.

## `jira`

**URI shape:** `jira://<alias>/projects/<project-key>/issues.jsonl` plus
`jira://<alias>/users.jsonl`.

**Obtain credentials:** three flavours, pick based on your Jira deployment:

- **Atlassian Cloud** (most common):
    - URL: `https://acme.atlassian.net`
    - Username: your Atlassian account email
    - API token: open
      <https://id.atlassian.com/manage-profile/security/api-tokens> →
      **Create API token** → label it `mfs` → copy. Goes into
      `JIRA_API_TOKEN`.

      ![Atlassian API tokens page](https://github.com/user-attachments/assets/578f84bc-f847-49a1-b318-495091f9c2cc)

      ![Atlassian create API token dialog](https://github.com/user-attachments/assets/2118ce0d-4901-497d-aea8-2ecbf430d848)

- **Atlassian Server / Data Center** (self-hosted):
    - URL: `https://jira.acme.internal`
    - Username: leave empty
    - API token: a Personal Access Token from your Jira profile →
      **Personal Access Tokens** → Create.
- **Older Server (no PAT support)**: username + password basic auth.
  Discouraged but supported.

API token permissions inherit the issuing user's permissions — restricted
projects look empty if the user can't see them.

**Minimum config:**

```toml
url = "https://acme.atlassian.net"
cloud = true
username = "alice@acme.com"
api_token = "env:JIRA_API_TOKEN"
projects = ["ENG", "OPS"]
max_read_rows = 50000

[[objects]]
match = "/projects/ENG"
text_fields = ["summary", "description"]
locator_fields = ["key"]
metadata_fields = ["status", "priority", "updated"]
```

**Start:**

```bash
mfs connector probe jira://acme --config ./jira.toml
mfs add jira://acme --config ./jira.toml
```

**Search or browse:**

```bash
mfs search "SSO regression" jira://acme/projects/ENG/issues.jsonl
mfs cat jira://acme/projects/ENG/issues.jsonl --locator '{"key":"ENG-1234"}'
```

**Common pitfalls:**

- Without `projects`, the connector enumerates all visible projects.
- The current flattened record uses `key` as the issue key field.
- Add `[[objects]]` text fields for issue rows unless your deployment has a
  generated config that already did this.
- API token permissions are the user's permissions; restricted projects can
  appear empty.

## `linear`

**URI shape:** `linear://<alias>/teams/<team-key>/issues.jsonl` plus
`linear://<alias>/users.jsonl`.

**Obtain credentials:** a **Personal API key** from Linear:

1. Open <https://linear.app> → **Settings → Personal → Security & access**
   and scroll to **Personal API keys**.

   ![Linear Personal API keys section](https://github.com/user-attachments/assets/d89e4d97-7fc6-47ce-8dab-436d3c6e3e18)

2. Click **New API key**, name it `mfs`, and choose the permission and team
   access that covers the teams you plan to sync.

   ![Linear Create API key dialog](https://github.com/user-attachments/assets/6e2a3f03-9dab-4753-a8e7-607f04190f8f)

3. Click **Create** and copy the value (starts with `lin_api_...`). That goes
   into `LINEAR_API_KEY`.

The key is tied to the issuing user's account. If you leave team access broad,
MFS can enumerate all teams visible to that user; if you restrict team access,
make sure the teams in `teams = [...]` are included.

**Minimum config:**

```toml
api_key = "env:LINEAR_API_KEY"
teams = ["ENG"]
```

The current connector has built-in presets for `issues.jsonl` and
`users.jsonl`, so `[[objects]]` is not required for normal issue and user
indexing. Add object rules only when you need to override the default fields.

**Start:**

```bash
mfs connector probe linear://workspace --config ./linear.toml
mfs add linear://workspace --config ./linear.toml
```

**Search or browse:**

```bash
mfs search "billing migration" linear://workspace/teams/ENG/issues.jsonl
mfs cat linear://workspace/teams/ENG/issues.jsonl --locator '{"identifier":"ENG-42"}'
```

**Common pitfalls:**

- The GraphQL API key is sent as the raw `Authorization` header value, not
  `Bearer <token>`.
- If `teams` is omitted, all visible teams are enumerated.
- If the Linear API key uses restricted team access, a team listed in TOML but
  missing from the key's team scope appears empty.
- Built-in presets index issue `title` and `description`, plus user `name` and
  `email`. Use custom `[[objects]]` rules only if you need different fields.
- The current flattened issue record contains `identifier`, not an `id` field.

## `zendesk`

**URI shape:** tickets are `zendesk://<alias>/tickets/records.jsonl`, ticket
comments are `/tickets/comments.jsonl`, users are `/users/records.jsonl`, and
organizations are `/organizations/records.jsonl`.

**Obtain credentials:** Zendesk uses **email + API token**. The auth layer
appends the literal `/token` suffix to the email automatically.

1. Open `https://<your-subdomain>.zendesk.com` → **Admin Center → Apps and
   integrations → APIs → Zendesk API**.
2. Toggle **Token Access** ON.
3. Click **Add API token** → label it `mfs` → copy the value. That goes
   into `ZENDESK_API_TOKEN`.

The token is bound to your user account; it inherits your role's
permissions.

**Minimum config:**

```toml
subdomain = "acme"
email = "alice@acme.com"
api_token = "env:ZENDESK_API_TOKEN"
max_read_rows = 50000
```

The connector schema in the wizard labels the email field as `username`, while
the current plugin reads `email` when building Zendesk Basic auth. Probe the
target config before syncing.

**Start:**

```bash
mfs connector probe zendesk://acme --config ./zendesk.toml
mfs add zendesk://acme --config ./zendesk.toml
```

**Search or browse:**

```bash
mfs search "billing dispute" zendesk://acme/tickets/records.jsonl
mfs search "refund policy" zendesk://acme/tickets/comments.jsonl
mfs cat zendesk://acme/tickets/records.jsonl --locator '{"id":12345}'
```

**Common pitfalls:**

- The built-in preset applies only to ticket records. Add `[[objects]]` if you
  need searchable comments, users, or organizations.
- Ticket comments are fetched per ticket and can be expensive on large tenants.
- Internal comments can be indexed if the API user can see them.
- `max_read_rows` caps each resource path.

## `hubspot`

**URI shape:** `hubspot://<alias>/<object>/records.jsonl`, for example
`hubspot://acme/contacts/records.jsonl`.

**Obtain credentials:** HubSpot uses a **Private App access token**.

1. Open <https://app.hubspot.com> → **Settings** (gear icon) →
   **Integrations → Private Apps** → **Create a private app**.

   If HubSpot shows the **Legacy Apps** page for the private app flow, click
   **Create legacy app** and choose the private app option for one account.

   ![HubSpot Create legacy app button](https://github.com/user-attachments/assets/eead666d-2e61-43b6-af9d-0a6453a4e96c)

2. Pick a name and description.

   ![HubSpot private app basic info form](https://github.com/user-attachments/assets/dffe73cb-d60c-41c2-89d2-eb7ce59c8954)

3. On the **Scopes** tab, enable the read scopes you need:
    - `crm.objects.contacts.read`
    - `crm.objects.companies.read`
    - `crm.objects.deals.read`
    - `tickets` (Service Hub only) — read tickets

   ![HubSpot selected read scopes](https://github.com/user-attachments/assets/92dd7813-bb96-4f91-8e63-67805f654483)

4. **Create app**. On the next screen copy the access token (`pat-na1-...`
   for the NA1 region, `pat-eu1-...` for EU). **The token is shown only
   once.** It goes into `HUBSPOT_ACCESS_TOKEN`.

**Minimum config:**

```toml
access_token = "env:HUBSPOT_ACCESS_TOKEN"
object_types = ["contacts", "companies", "deals", "tickets"]
max_read_rows = 50000

[[objects]]
match = "/contacts"
text_fields = ["firstname", "lastname", "email", "jobtitle"]
locator_fields = ["id"]
```

**Start:**

```bash
mfs connector probe hubspot://acme --config ./hubspot.toml
mfs add hubspot://acme --config ./hubspot.toml
```

**Search or browse:**

```bash
mfs search "customer health" hubspot://acme/contacts/records.jsonl
mfs cat hubspot://acme/contacts/records.jsonl --locator '{"id":"12345"}'
```

**Common pitfalls:**

- Add `[[objects]]`; HubSpot has no built-in row preset.
- If `object_types` is omitted, the plugin probes common default objects and
  skips objects the portal rejects.
- HubSpot properties are flattened from the `properties` envelope to top-level
  fields.
- Engagement records such as calls, notes, and emails are not included by this
  connector.

## `bigquery`

**URI shape:** `bigquery://<alias>/<dataset>/tables/<table>/rows.jsonl` for
rows and `schema.json` for table schemas.

**Obtain credentials:** BigQuery uses **Application Default Credentials
(ADC)** — there is no token in the connector TOML; the credentials must be
visible to the server process. Three common ways:

1. **Service account JSON file** (most common in production):

    In Google Cloud Console, confirm **BigQuery API** is enabled, then create
    or choose a service account for the dataset access boundary.

    ![Google Cloud BigQuery API page](https://github.com/user-attachments/assets/fd034ea0-4607-4e74-b65a-106460830889)

    ![Google Cloud service accounts page](https://github.com/user-attachments/assets/6d7dfb43-f7f9-46dc-a049-ed9cc251afea)

    ![Google Cloud create service account form](https://github.com/user-attachments/assets/d8275bc9-1792-44fd-951a-18dd3cc789fd)

    ```bash
    export GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa.json
    mfs-server run
    ```

2. **`gcloud auth application-default login`** (dev / single-user
   machines) — creates
   `~/.config/gcloud/application_default_credentials.json`.

3. **Workload Identity** on GKE / Cloud Run — ADC is automatic.

The service account or user only needs `roles/bigquery.dataViewer` on the
datasets to be indexed.

**Minimum config:**

```toml
project = "analytics-prod"
datasets = ["events", "kb"]
max_read_rows = 100000

[[objects]]
match = "/kb/tables/articles"
text_fields = ["title", "body_markdown"]
locator_fields = ["article_id"]
```

BigQuery credentials come from Application Default Credentials in the server
environment, such as `GOOGLE_APPLICATION_CREDENTIALS`.

**Start:**

```bash
mfs connector probe bigquery://analytics --config ./bigquery.toml
mfs add bigquery://analytics --config ./bigquery.toml
```

**Search or browse:**

```bash
mfs search "refund event" bigquery://analytics/events/tables/user_events/rows.jsonl
mfs search "email column" bigquery://analytics --kind schema_summary
mfs cat bigquery://analytics/kb/tables/articles/rows.jsonl --locator '{"article_id":"a-123"}'
```

**Common pitfalls:**

- Add `[[objects]]`; BigQuery table rows need text fields and locator fields.
- ADC must be available to the server process, not only the CLI shell.
- The connector uses `list_rows`; `max_read_rows` caps large tables.
- BigQuery has no primary-key concept for most tables, so choose stable
  locator fields explicitly.

## `snowflake`

**URI shape:** `snowflake://<alias>/<DATABASE>/<SCHEMA>/tables/<TABLE>/rows.jsonl`
and `schema.json`.

**Obtain credentials:** three modes, selected via `auth_mode`. Key-pair is
the default and recommended for production.

**Key-pair** (`auth_mode = "key-pair"`, default):

```bash
openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out rsa_key.p8 -nocrypt
openssl rsa -in rsa_key.p8 -pubout -out rsa_key.pub
```

Register the public key on the Snowflake user:

```sql
ALTER USER mfs_reader SET RSA_PUBLIC_KEY='<contents of rsa_key.pub minus header/footer>';
```

The private key file path goes into `credential_ref` (e.g.
`credential_ref = "file:/etc/mfs/snowflake/rsa_key.p8"`). If the key has a
passphrase, set `private_key_passphrase_ref` too.

**Password** (`auth_mode = "password"`): `credential_ref` carries the
Snowflake password (prefer an `env:` or `file:` ref). Snowflake is
progressively tightening password login — check your account's MFA policy
before relying on this in production.

**PAT** (`auth_mode = "pat"`): issue a Programmatic Access Token for the
user in the Snowflake UI, attach a network policy that includes your
egress IPs, and put the token into `credential_ref`. Rotation is just
"issue a new PAT, replace the secret" — no key-pair re-registration.

![Snowflake Users and roles page](https://github.com/user-attachments/assets/102fd1cf-3841-4606-918e-0cff54f356c2)

![Snowflake programmatic access tokens section](https://github.com/user-attachments/assets/7fae2256-cf98-4750-8236-0fca285ab69f)

![Snowflake new programmatic access token dialog](https://github.com/user-attachments/assets/9a2ba38a-8bed-4c9b-8c6b-1b6dcafd7909)

The user should have a read-only role; grant `USAGE` on the warehouse,
database, schemas, plus `SELECT` on the in-scope tables.

**Minimum config:**

```toml
account = "abcdefg-xy12345"
user = "mfs_reader"
warehouse = "mfs_wh"
role = "mfs_reader_role"
database = "PROD"
credential_ref = "file:/etc/mfs/snowflake/rsa_key.p8"

[[objects]]
match = "/PROD/PUBLIC/tables/TICKETS"
text_fields = ["TITLE", "DESCRIPTION"]
locator_fields = ["ID"]
```

`credential_ref` must resolve to a PEM PKCS#8 RSA private key in the
default key-pair mode. If the key is encrypted, set
`private_key_passphrase_ref`.

**Start:**

```bash
mfs connector probe snowflake://analytics --config ./snowflake.toml
mfs add snowflake://analytics --config ./snowflake.toml
```

**Search or browse:**

```bash
mfs search "billing event" snowflake://analytics/PROD/PUBLIC/tables/TICKETS/rows.jsonl
mfs search "EMAIL column" snowflake://analytics --kind schema_summary
mfs cat snowflake://analytics/PROD/PUBLIC/tables/TICKETS/rows.jsonl --locator '{"ID":12345}'
```

**Common pitfalls:**

- The connector supports `auth_mode` of `key-pair` (default), `password`,
  or `pat`. Key-pair is the recommended production path; password is
  subject to Snowflake's tightening login policies.
- Snowflake folds unquoted identifiers to uppercase; paths and locators must
  match actual returned casing.
- Warehouses may auto-resume on first query, adding startup latency.
- Add `[[objects]]`; table rows need text fields and locator fields.

## `s3`

**URI shape:** `s3://<alias>/<object-key>`. The tree mirrors object keys in the
configured bucket and prefix.

**Obtain credentials:** S3-compatible — the same connector covers AWS S3,
Cloudflare R2, GCS S3 interop, and MinIO via `endpoint_url`.

- **AWS S3**: create an IAM user under **Security credentials → Access
  keys → Create access key**, or use STS temporary credentials. boto3
  reads `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` automatically; if
  those are set in the server environment, you can omit them from the
  TOML. Minimum IAM policy:

    ```json
    {
      "Version": "2012-10-17",
      "Statement": [{
        "Effect": "Allow",
        "Action": ["s3:GetObject", "s3:ListBucket"],
        "Resource": ["arn:aws:s3:::my-bucket", "arn:aws:s3:::my-bucket/*"]
      }]
    }
    ```

- **Cloudflare R2**: R2 dashboard → **Manage R2 API Tokens → Create API
  Token** with Object Read scope. `endpoint_url =
  "https://<account-id>.r2.cloudflarestorage.com"`, `region = "auto"`.

- **Google Cloud Storage (S3 interop)**: IAM service-account HMAC key,
  `endpoint_url = "https://storage.googleapis.com"`.

- **MinIO (self-hosted)**: service's access key + secret key,
  `endpoint_url = "https://minio.internal:9000"` (or whatever your MinIO
  URL is).

**Minimum config:**

```toml
bucket = "acme-docs"
prefix = "engineering/rfc/"
region = "us-west-2"
access_key_id = "env:AWS_ACCESS_KEY_ID"
secret_access_key = "env:AWS_SECRET_ACCESS_KEY"
```

Set `endpoint_url` for R2, GCS S3 interop, MinIO, or another compatible
endpoint.

**Start:**

```bash
mfs connector probe s3://acme-docs --config ./s3.toml
mfs add s3://acme-docs --config ./s3.toml
```

**Search or browse:**

```bash
mfs search "retention policy" s3://acme-docs/engineering/rfc/
mfs cat s3://acme-docs/engineering/rfc/rfc-001.md --range 1:80
mfs export s3://acme-docs/engineering/rfc/rfc-001.pdf /tmp/rfc-001.pdf
```

**Common pitfalls:**

- `prefix` is exact. Use a trailing slash when you mean a directory-like
  prefix.
- Versioned buckets expose only the latest version.
- Very large PDFs or Office documents can be expensive to convert.
- IAM must allow both `ListBucket` and `GetObject` for the scoped bucket/prefix.

## `gdrive`

**URI shape:** `gdrive://<alias>/<folder>/<file>`. Google-native Docs, Sheets,
and Slides are exported to text or CSV-like content by the plugin.

**Obtain credentials:** Google Drive uses a **user OAuth token JSON** —
the `token.json` produced by Google's OAuth flow, containing
`refresh_token` / `client_id` / `client_secret`. Service-account keys
are not supported by the current plugin.

1. GCP Console → **APIs & Services → Library** → enable **Google Drive
   API**.

   ![Google Cloud Drive API page](https://github.com/user-attachments/assets/523b9021-f809-4b1f-a1ad-16703739c409)

2. **Credentials → Create Credentials → OAuth client ID** → Application
   type: **Desktop app** → **Download JSON** (the client credentials
   file).

   ![Google Cloud Create credentials menu](https://github.com/user-attachments/assets/fc27a2aa-8eaf-4338-ada4-067be970857d)

   ![Google Cloud OAuth desktop client form](https://github.com/user-attachments/assets/2872163a-18d2-4592-ac43-b7ffc49693ed)

3. Run Google's OAuth flow once on a machine with a browser (e.g. the
   `google-auth-oauthlib` `InstalledAppFlow.run_local_server` snippet)
   requesting scope `https://www.googleapis.com/auth/drive.readonly`.
   The flow writes a `token.json` next to the client JSON.
4. Copy `token.json` to the server and reference it from the connector
   TOML. The authorized user must already be able to see the files /
   folders you want indexed (own files + files explicitly shared with
   them).

If you also configure the [`gmail`](#gmail) connector, request
`https://www.googleapis.com/auth/gmail.readonly` in the same consent
step — the same `token.json` then drives both connectors.

**Minimum config:**

```toml
token = "file:/home/x/.mfs/gdrive-token.json"
```

The plugin builds `google.oauth2.credentials.Credentials` from the
configured `token` value when it is a string (bare access token), or from
`Credentials.from_authorized_user_info` when the parsed TOML value is an
object (inline token JSON). The most common form is the `file:`
reference above. Probe the connector in the target server before
syncing.

**Limiting scope (large Drives):** the connector enumerates the whole Drive the
credential can see. For a big account, index recent files first — estimate the size
(optionally with a `since` date via `/v1/connectors/estimate`), then
`mfs add gdrive://<alias> --since <date>` indexes only files modified on/after `<date>`;
older files are left untouched (never deleted) and can be added later by lowering
`--since`.

**Start:**

```bash
mfs connector probe gdrive://engineering --config ./gdrive.toml
mfs add gdrive://engineering --config ./gdrive.toml
```

**Search or browse:**

```bash
mfs search "roadmap" gdrive://engineering/Product/
mfs cat gdrive://engineering/Product/Roadmap.txt --range 1:80
mfs export gdrive://engineering/Product/Design.pdf /tmp/design.pdf
```

**Common pitfalls:**

- The authorized user can only see files they own or that are
  explicitly shared with them.
- Headless server: the OAuth flow needs a browser the first time. Run
  it on a workstation, then copy `token.json` to the server.
- 401/403s usually mean the token was revoked or the consent did not
  include `drive.readonly`. Re-run the OAuth flow.
- Google-native files are exported; comments are not indexed.
- The current plugin does not expose a folder-token scope field; it walks files
  visible to the credential.

## `feishu`

**URI shape:** chats are
`feishu://<alias>/chats/<name>__<chat-id>/messages.jsonl`. Docx documents are
`feishu://<alias>/docs/<title>__<doc-token>.md`.

**Obtain credentials:** Feishu / Lark needs an **App ID** + **App Secret**
from the Lark Developer Console.

1. Go to <https://open.feishu.cn/app> (feishu / China) or
   <https://open.larksuite.com/app> (lark / overseas).

   ![Feishu Create Custom App button](https://github.com/user-attachments/assets/1099fb06-aa59-4b5f-ba1b-543f7551e508)

2. **Create Custom App** → name + icon. Note the **App ID** (`cli_...`) and
   **App Secret**.

   ![Feishu Create custom app dialog](https://github.com/user-attachments/assets/99a2a7e2-769a-49b4-b3ba-2e8ee2409bea)

   ![Feishu app credentials section](https://github.com/user-attachments/assets/478ddc09-79e5-4d8c-a3c2-e441ebb37c66)

3. **Permissions & Scopes** → add the read scopes below, then **Version
   Management & Release** → request admin approval if your org requires it.

   ![Feishu Permissions and Scopes page](https://github.com/user-attachments/assets/4183bc00-e351-4576-9f19-8520290d114c)

   ![Feishu Version Management and Release page](https://github.com/user-attachments/assets/f1fa494a-46eb-4934-a65f-97fbd9f6eef8)

### User mode (default, recommended)

The app indexes everything the authorizing user can see. Add the scopes as
**User Scopes**: `im:chat:readonly`, `im:message.group_msg:get_as_user`,
`im:message.p2p_msg:get_as_user`, `drive:drive:readonly`,
`docx:document:readonly`, `contact:user.id:readonly`.

Add the connector — the wizard runs a one-time browser authorization inline:

```bash
mfs-server connector add feishu://workspace
```

Open the printed URL in a browser and approve — **this consent must be done by a
person and can't be automated** (it's the OAuth user-authorization step). The
connector is then ready.

The token refreshes automatically **while the connector is actively synced** (each
sync renews it, no intervention needed). If it goes unused for several days the
authorization expires; the next use then reports that re-authorization is needed. To
re-authorize, run `connector auth` and again have a user approve the printed URL in a
browser — existing indexed data is unaffected:

```bash
mfs-server connector auth feishu://workspace
```

Minimum config (the wizard writes this; keep the secret as an env ref):

```toml
app_id = "cli_a1b2c3d4"
app_secret = "env:FEISHU_APP_SECRET"
region = "feishu"          # or "lark"
auth = "user"
```

### Tenant mode (app-only bot)

Set `auth = "tenant"`. The app acts as itself and sees only chats it has been
added to and docs/folders shared with it — add the bot to a chat by
`@mentioning` it. Add the same scopes as **Bot Scopes**.

```toml
app_id = "cli_a1b2c3d4"
app_secret = "env:FEISHU_APP_SECRET"
region = "feishu"
auth = "tenant"
docs_folder_token = "fldcn..."   # optional: limit docs to one shared folder
max_read_rows = 50000
```

**Search or browse:**

```bash
mfs search "deploy failed" feishu://workspace/chats/
mfs search "quarterly roadmap" feishu://workspace/docs/
mfs cat feishu://workspace/docs/Roadmap__doccnxxx.md --range 1:80
```

**Notes:**

- **p2p single chats** can't be auto-listed (Feishu API limit). Include them
  with `extra_chats` — by `oc_...` chat id, or by the partner's `ou_...` open_id.
- **docs:** only docx is indexed. In user mode with no `docs_folder_token` /
  `extra_docs`, the connector enumerates your whole My Space; narrow it with
  `docs_folder_token`, or name specific docs with `extra_docs`. In tenant mode the
  app only sees docs/folders shared with it.
- **scope by time:** user mode can enumerate your entire My Space — for a large
  account, estimate first (optionally with a `since` date) and use
  `mfs add feishu://<alias> --since <date>` to index only recently-changed docs.
  Older docs are left untouched (never deleted) and can be added later by lowering
  `--since`.
- **region:** `feishu` and `lark` are separate registries — an app from one
  can't authorize against the other.
