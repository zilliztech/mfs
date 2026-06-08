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
| [`salesforce`](#salesforce) | `salesforce://alias/<SObject>/records.jsonl` | Salesforce sObject records. |
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
2. Repository access: **Only select repositories** → pick the ones to index.
3. Repository permissions:
    - **Contents** → Read-only
    - **Issues** → Read-only
    - **Pull requests** → Read-only
    - **Metadata** → Read-only (always required)
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
index_meta = true
max_read_rows = 5000
```

Set `GITHUB_TOKEN` in the server environment for authenticated requests. The
current plugin reads that environment variable directly when building GitHub
API headers.

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
- Private repositories need `GITHUB_TOKEN` in the server process environment.
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
2. Left sidebar → **Bot** → click **Add Bot** → **Reset Token** → copy the
   token. This is the value that goes into `DISCORD_BOT_TOKEN`.
3. On the same Bot page, scroll down to **Privileged Gateway Intents** and
   enable **Message Content Intent**. Without this the bot connects fine
   but every message comes back with empty `content`.
4. Left sidebar → **OAuth2** → **URL Generator**:
    - Scopes: `bot`
    - Bot Permissions: `View Channels`, `Read Message History`
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

**Obtain credentials:** Gmail uses **Google OAuth 2.0** with a downloaded
credentials JSON:

1. Open <https://console.cloud.google.com> → create or pick a project.
2. **APIs & Services → Library → Gmail API** → click **Enable**.
3. **APIs & Services → Credentials → Create Credentials → OAuth client
   ID** → Application type: **Desktop app** → name it → **Download JSON**.
4. Save the file somewhere the server can read it
   (e.g. `~/.mfs/gmail-credentials.json`).
5. The first `mfs add` opens a browser to authorize; the resulting
   `token.json` is cached next to the credentials file.

Required OAuth scope: `https://www.googleapis.com/auth/gmail.readonly`. The
connector only calls `messages.list` + `messages.get`; it doesn't send or
modify mail.

**Minimum config:**

```toml
token = "env:GMAIL_ACCESS_TOKEN"
labels = ["INBOX"]
max_read_rows = 5000
```

The current plugin builds `google.oauth2.credentials.Credentials` from the
configured `token` value when it is a string, or from
`Credentials.from_authorized_user_info` when the parsed TOML value is an object.
Probe the connector in the target server before syncing.

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
2. Pick a name and the target workspace.
3. Under **Capabilities**, tick `Read content` (the other capabilities are
   not needed for MFS).
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

1. Open <https://linear.app> → **Settings → Account → API → Personal
   API keys**.
2. Click **Create new key**, name it `mfs`, copy the value (starts with
   `lin_api_...`). That goes into `LINEAR_API_KEY`.

The key inherits the issuing user's workspace access (all teams and
projects visible to them in the Linear UI).

**Minimum config:**

```toml
api_key = "env:LINEAR_API_KEY"
teams = ["ENG"]

[[objects]]
match = "/teams/ENG"
text_fields = ["title", "description"]
locator_fields = ["identifier"]
metadata_fields = ["state", "priority", "updatedAt"]
```

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
- Add `[[objects]]`; the current plugin has no built-in Linear preset.
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

## `salesforce`

**URI shape:** `salesforce://<alias>/<SObject>/records.jsonl` and
`salesforce://<alias>/<SObject>/schema.json`.

**Obtain credentials:** **username + password + security token** (SOAP
login). OAuth flows aren't supported by this connector yet.

1. **Username + Password**: your normal Salesforce login.
2. **Security token**: log into Salesforce → **Settings → My Personal
   Information → Reset My Security Token**. A new token is emailed to you.
   Required whenever API access is from outside the org's trusted IP range.
3. **Instance URL**: visible in the URL bar after login (e.g.
   `https://acme.my.salesforce.com`). Only needed when reusing a
   `session_id`.
4. **Domain**: `login` for production, `test` for sandbox.

If you already have a Salesforce session, set `session_id` + `instance_url`
and the plugin skips the username/password/security-token login flow.

**Minimum config:**

```toml
username = "alice@acme.com"
password = "env:SF_PASSWORD"
security_token = "env:SF_SECURITY_TOKEN"
domain = "login"
objects = ["Account", "Contact", "Opportunity", "Case"]

[[objects]]
match = "/Account"
text_fields = ["Name", "Description"]
locator_fields = ["Id"]
metadata_fields = ["LastModifiedDate"]
```

If `session_id` is set, the plugin uses `instance_url` plus `session_id`
instead of username/password/security-token login.

**Start:**

```bash
mfs connector probe salesforce://acme --config ./salesforce.toml
mfs add salesforce://acme --config ./salesforce.toml
```

**Search or browse:**

```bash
mfs search "renewal risk" salesforce://acme/Account/records.jsonl
mfs cat salesforce://acme/Account/records.jsonl --locator '{"Id":"001AB..."}'
```

**Common pitfalls:**

- Add `[[objects]]` text fields; Salesforce has no built-in row preset.
- Field-level security controls which fields the API user can read.
- Custom object names usually end in `__c` and must be included in `objects`.
- Use `domain = "test"` for sandboxes.

## `hubspot`

**URI shape:** `hubspot://<alias>/<object>/records.jsonl`, for example
`hubspot://acme/contacts/records.jsonl`.

**Obtain credentials:** HubSpot uses a **Private App access token**.

1. Open <https://app.hubspot.com> → **Settings** (gear icon) →
   **Integrations → Private Apps** → **Create a private app**.
2. Pick a name and description.
3. On the **Scopes** tab, enable the read scopes you need:
    - `crm.objects.contacts.read`
    - `crm.objects.companies.read`
    - `crm.objects.deals.read`
    - `tickets` (Service Hub only) — read tickets
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

**Obtain credentials:** Google Drive uses **OAuth credentials**. Two flows:

**Service account** (recommended for shared / production access):

1. GCP Console → **APIs & Services → Library** → enable **Google Drive
   API**.
2. **Credentials → Create Credentials → Service account** → name + role
   (`Viewer` is enough for drive read).
3. On the service account → **Keys → Add key → JSON** → download.
4. **Share** each Drive folder you want indexed with the service account's
   email (`<account>@<project>.iam.gserviceaccount.com`). Without that
   share, the account can't see the folder.

**User OAuth** (a single user's visibility):

1. Same GCP console: OAuth client ID, application type **Desktop app**,
   download the JSON.
2. First-run browser flow on a machine with a display; resulting
   `token.json` is cached next to credentials.

**Minimum config:**

```toml
token = "env:GDRIVE_ACCESS_TOKEN"
```

The current plugin builds `google.oauth2.credentials.Credentials` from the
configured `token` value when it is a string, or from
`Credentials.from_authorized_user_info` when the parsed TOML value is an object.
Probe the connector in the target server before syncing.

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

- The credential can only see files shared with it.
- Google-native files are exported; comments are not indexed.
- The current plugin does not expose a folder-token scope field; it walks files
  visible to the credential.

## `feishu`

**URI shape:** chats are
`feishu://<alias>/chats/<name>__<chat-id>/messages.jsonl`. Docx documents are
`feishu://<alias>/docs/<title>__<doc-token>.md`.

**Obtain credentials:** Feishu / Lark needs an **App ID** + **App Secret**
from the Lark Developer Console, plus one of two auth modes.

Create the app:

1. Go to <https://open.feishu.cn/app> (CN) or
   <https://open.larksuite.com/app> (US).
2. **Create Custom App** → name + icon.
3. Note the **App ID** (`cli_...`) and **App Secret**.

Then pick an auth mode:

**Tenant / bot** (`auth = "tenant"`): the app acts as itself. Easier to set
up, but only sees chats and docs explicitly shared with it.

- Developer console → **Permissions & Scopes**, add as **Bot Scopes**:
    - `im:message:readonly` — read messages
    - `im:chat:readonly` — list chats
    - `docx:document:readonly` — read docx documents
    - `drive:drive:readonly` — list drive items
- **Version Management & Release** → request approval from your tenant
  admin.
- Add the bot to each chat by mentioning it (`@bot-name`) or pinning it
  via group admin settings.

**User OAuth** (`auth = "user"`, recommended for full visibility): the app
acts on behalf of a real user and sees everything that user sees.

- Same scopes as above but as **User Scopes**.
- Run the bundled auth helper once on a machine with a browser:

    ```bash
    uv run python -m mfs_server.connectors.feishu.auth_login \
      --app-id cli_a1b2c3d4 \
      --app-secret <secret> \
      --region cn
    ```

    It opens a browser for the user to authorize, then writes the
    resulting `oauth.json` to `$MFS_HOME/feishu.oauth.json` by default.
- The plugin refreshes the token on every connect and atomically rotates
  the refresh_token — Feishu refresh tokens are one-shot, so the plugin
  must own R/W of that file. That's why `oauth_state_file` is a path, not
  a `credential_ref`.

**Minimum config for tenant/bot mode:**

```toml
app_id = "cli_a1b2c3d4"
app_secret = "env:FEISHU_APP_SECRET"
region = "feishu"
auth = "tenant"
docs_folder_token = "fldcn..."
max_read_rows = 50000
```

**Minimum config for user OAuth mode:**

```toml
auth = "user"
oauth_state_file = "/home/mfs/feishu.oauth.json"
```

Create the OAuth state file with the bundled helper:

```bash
python -m mfs_server.connectors.feishu.auth_login \
  --app-id cli_a1b2c3d4 \
  --app-secret-env FEISHU_APP_SECRET \
  --region feishu \
  --output /home/mfs/feishu.oauth.json
```

**Start:**

```bash
mfs connector probe feishu://workspace --config ./feishu.toml
mfs add feishu://workspace --config ./feishu.toml
```

**Search or browse:**

```bash
mfs search "deploy failed" feishu://workspace/chats/
mfs search "quarterly roadmap" feishu://workspace/docs/
mfs cat feishu://workspace/chats/eng__oc_xxx/messages.jsonl --locator '{"message_id":"om_abc123"}'
mfs cat feishu://workspace/docs/Roadmap__doccnxxx.md --range 1:80
```

**Common pitfalls:**

- Current code uses `auth = "tenant"` or `auth = "user"` and region values
  `feishu` or `lark`.
- Tenant mode only sees chats the bot is in and docs shared with the app.
- User mode refresh tokens rotate; `oauth_state_file` must be writable by the
  server process.
- Only docx documents are exposed under `docs/` by the current plugin.
