# Connectors

MFS connectors expose non-local sources as file-like URI trees. After a source is
registered, the same CLI verbs work across local files, databases, object stores,
issue trackers, CRMs, chat systems, and document systems:

```bash
mfs search "release checklist" github://owner/repo
mfs ls slack://acme/channels
mfs cat postgres://prod-db/public/tickets/rows.jsonl --locator '{"id": 12345}'
```

Use this page to choose a connector, prepare its TOML configuration, probe it,
add or update it, inspect its sync state, and remove it from MFS when needed.
If you already chose a scheme and need to author safe runnable TOML quickly,
use [Connector Configuration Recipes](connector-config-recipes.md).

!!! note "Optional connector extras"
    The `file` connector is always imported by the server registry. The other
    built-in schemes are imported lazily and skipped if their optional
    dependencies are not installed in that server environment. A scheme listed
    here is built into the codebase, but it is not guaranteed to be available on
    every installed server. Probe the connector in the target environment before
    relying on it.

## Built-in catalog

The tables below use the server registry for the scheme list and the server
connector schemas plus the [Connector Reference](connector-reference.md) for
summaries, URI hints, config fields, tree shapes, locator examples, and
connector-specific pitfalls.

### Local files

| Scheme | Use it for | Start with | TOML and credentials | Reference |
|---|---|---|---|---|
| `file` | Local directory trees. The CLI accepts bare paths. | `mfs add ./docs` or `mfs add /abs/path` | No TOML is needed for simple paths. Use `--config` for options such as `max_file_bytes` or `[[objects]]` rules that mark patterns non-indexable. | [details](connector-reference.md#file) |

### Web, object, and document stores

| Scheme | Use it for | URI hint | TOML and credentials | Reference |
|---|---|---|---|---|
| `web` | Crawl HTTP(S) pages and index converted markdown. | `web://my-docs` | `start_urls` is required. Use `allowed_domains` to limit scope and `max_pages` to cap the crawl. | [details](connector-reference.md#web) |
| `s3` | Objects under an S3, R2, GCS interop, MinIO, or compatible bucket prefix. | `s3://my-bucket` | Configure `bucket`, optional `prefix`, `region`, and `endpoint_url`. Put access keys behind `env:` references. | [details](connector-reference.md#s3) |
| `gdrive` | Google Drive folders and files visible to the credential. | `gdrive://my-drive` | `token` is an access token string or a TOML object accepted by `Credentials.from_authorized_user_info`. Probe the target server before syncing. | [details](connector-reference.md#gdrive) |
| `notion` | Notion pages and data sources shared with an internal integration. | `notion://workspace` | `token` is the internal integration token. Share each page or data source tree with the integration before syncing. | [details](connector-reference.md#notion) |

### Databases and warehouses

| Scheme | Use it for | URI hint | TOML and credentials | Reference |
|---|---|---|---|---|
| `postgres` | Postgres tables, per-row indexing, and cursor-based incremental sync. | `postgres://prod-db` | `dsn` is required. Add `[[objects]]` blocks for every table that should become searchable. | [details](connector-reference.md#postgres) |
| `mysql` | MySQL tables, per-row indexing, and cursor-based incremental sync. | `mysql://prod-db` | Configure `host`, `port`, `database`, `user`, and `password`. Add `[[objects]]` per table. | [details](connector-reference.md#mysql) |
| `mongo` | MongoDB collections, one document per record. | `mongo://prod-cluster` | Configure `uri` and `database`. Add `[[objects]]` per collection because document shapes vary. | [details](connector-reference.md#mongo) |
| `bigquery` | BigQuery tables through Application Default Credentials. | `bigquery://analytics` | Configure `project` and `datasets`; credentials must be available to the server through ADC. Use `[[objects]]` for table text and locators. | [details](connector-reference.md#bigquery) |
| `snowflake` | Snowflake tables using key-pair JWT authentication. | `snowflake://analytics` | Configure account, user, warehouse, and a `credential_ref` such as `file:/abs/path/key.p8`. Add `[[objects]]`; Snowflake paths and fields usually come back uppercase. | [details](connector-reference.md#snowflake) |

### Code, issues, and work tracking

| Scheme | Use it for | URI hint | TOML and credentials | Reference |
|---|---|---|---|---|
| `github` | GitHub repository code plus issues and pull requests. | `github://owner/repo` | Set `repo = "owner/name"` in TOML. The current plugin reads `GITHUB_TOKEN` from the server environment for authenticated requests; `branch` is optional. | [details](connector-reference.md#github) |
| `jira` | Jira issues from Atlassian Cloud or Server/Data Center. | `jira://acme` | Configure `url`, `cloud`, and `api_token`; Cloud also needs `username`. Use `projects` to avoid unbounded tenant-wide syncs. | [details](connector-reference.md#jira) |
| `linear` | Linear issues across selected teams or the whole workspace. | `linear://workspace` | `api_key` is required. `teams` can narrow the sync to specific teams. | [details](connector-reference.md#linear) |

### CRM and support

| Scheme | Use it for | URI hint | TOML and credentials | Reference |
|---|---|---|---|---|
| `hubspot` | HubSpot CRM objects such as contacts, companies, deals, tickets, and custom objects. | `hubspot://acme` | `access_token` is required. `object_types` can force the object set; otherwise the connector probes common objects and skips inaccessible ones. | [details](connector-reference.md#hubspot) |
| `zendesk` | Zendesk tickets, ticket comments, users, and organizations. | `zendesk://acme` | Configure `subdomain`, `email`, and `api_token`; `base_url` is only for unusual deployments. The wizard schema currently labels this field `username`, so probe before syncing. | [details](connector-reference.md#zendesk) |

### Chat, mail, and collaborative docs

| Scheme | Use it for | URI hint | TOML and credentials | Reference |
|---|---|---|---|---|
| `slack` | Slack channels, messages, threads, and workspace users. | `slack://my-workspace` | `token` is required. `channel_types`, `oldest`, and `max_read_rows` control scope and size. | [details](connector-reference.md#slack) |
| `discord` | Discord guild text channels and active threads. | `discord://my-guild` | Configure a bot `token`, `guild_id`, and optional `max_read_rows`. | [details](connector-reference.md#discord) |
| `gmail` | Gmail messages grouped by `threadId` under labels. | `gmail://inbox` | `token` is an access token string or a TOML object accepted by `Credentials.from_authorized_user_info`. `labels` can restrict which labels are indexed. | [details](connector-reference.md#gmail) |
| `feishu` | Feishu/Lark messenger chats and docs. | `feishu://my-workspace` | Current code uses `auth = "tenant"` or `"user"` and `region = "feishu"` or `"lark"`; optional fields narrow docs folders, chats, and docs. | [details](connector-reference.md#feishu) |

## Lifecycle commands

These commands are the current v0.4 `mfs` CLI forms.

| Task | Command | Notes |
|---|---|---|
| Check server, connectors, and jobs | `mfs status` | Prints the server status envelope. Do not pass a URI to `mfs status`. |
| Probe without registering | `mfs connector probe <target> --config <file.toml>` | Calls `/v1/connectors/probe` with `target` and optional `config`. Use this after preparing or editing TOML. |
| Add a local path | `mfs add ./docs` | Bare paths are accepted. If the server runs on another machine, the CLI may bundle and upload the tree unless `--no-upload` is set. |
| Add an external connector | `mfs add postgres://prod-db --config ./postgres.toml` | For non-local targets, `mfs add` first runs a zero-billing estimate and asks for confirmation unless `-y` or `--yes` is set. |
| Add without an interactive prompt | `mfs add web://docs --config ./web.toml --yes` | Useful for automation after you understand the estimated object, chunk, and token counts. |
| Inspect an add job | `mfs job show JOB_ID` | `mfs add` returns after queueing a job. Use the job id to inspect terminal state. |
| Re-sync with a cursor/date | `mfs add <target> --since <cursor-or-date>` | Only connectors with a time cursor can use this meaningfully. |
| Force a full re-index | `mfs add <target> --force-index` | Ignores caches and fingerprints. The CLI also exposes `--full` as an alias. |
| List registered connectors | `mfs connector list` | Reads connector rows from `/v1/status`. |
| Inspect one connector | `mfs connector inspect <target>` | Calls `/v1/connectors/inspect?target=...` and prints JSON. |
| Update connector config | `mfs connector update <target> --config <file.toml>` | Applies config to an existing connector and queues a sync through `/v1/add` with `update: true`. |
| Remove a connector | `mfs connector remove <target>` | Prompts before deleting MFS-owned connector metadata, artifacts, and index data. Target must be a registered connector root, not a child path. |
| Remove through the alias | `mfs remove <target>` | Alias for `mfs connector remove <target>`. `-y` or `--yes` skips the prompt. |
| List jobs | `mfs job list` | Shows recent ingest jobs. |
| Show one job | `mfs job show <job_id>` | Use the job id returned by add or update. |
| Cancel a job | `mfs job cancel <job_id>` | Cancels a running or queued job when the server accepts the cancel request. |

!!! warning "Remove is destructive inside MFS"
    Removing a connector does not delete data from the source system, but it does
    drop the connector's MFS metadata, artifacts, and index data. Keep the
    confirmation prompt enabled unless you are running a deliberate automation.

### Estimate behavior

There is no standalone estimate flag in the current CLI. Instead, when the
target is not an existing local path and `--yes` is not set, `mfs add` posts to
`/v1/connectors/estimate` before queueing work. The estimate is based on
metadata plus a local chunker/tokenizer dry run and does not make embedding API
calls. The CLI prints discovered object count plus approximate chunk and token
counts, then asks whether to continue.

## Configuration

Connector configuration is TOML loaded by `--config <file>`. The CLI parses the
TOML and sends it as the `config` object in the API request.

```bash
mfs connector probe slack://acme --config ./slack.toml
mfs add slack://acme --config ./slack.toml
mfs connector update slack://acme --config ./slack.toml
```

### Keep secrets out of TOML

Prefer `env:` and `file:` references over plaintext secrets.

```toml
token = "env:SLACK_BOT_TOKEN"
dsn = "env:PG_PROD_DSN"
credential_ref = "file:/etc/mfs/snowflake/rsa_key.p8"
```

- `env:VAR_NAME` is resolved from the server process environment when the
  connector is constructed.
- `file:/abs/path` reads the file contents, stripping trailing whitespace. The
  path must be absolute.
- Plaintext values work, but they remain on disk in connector TOML and can leak
  through backups or accidental commits.

!!! warning "Credential safety"
    If you use `env:VAR_NAME`, set the variable in the environment that starts
    the MFS server, not just in the shell that runs the client command. If you
    use `file:/abs/path`, keep the file outside the repository and restrict its
    permissions.

For the process boundary behind these references and first missing-credential
checks, see [Auth and Secrets](auth-and-secrets.md#connector-credentials).

### Use `[[objects]]` for row-oriented sources

Table and collection connectors need object rules so MFS knows which fields are
searchable text and which fields reopen an exact record. This is especially
important for `postgres`, `mysql`, `mongo`, `bigquery`, and `snowflake`.

```toml
[[objects]]
match = "/public/tickets"
text_fields = ["title", "description"]
locator_fields = ["id"]
metadata_fields = ["status", "updated_at"]
```

- `match` selects the table or collection path used by that connector.
- `text_fields` become searchable content. Use prose-like fields such as
  titles, descriptions, bodies, notes, and comments.
- `locator_fields` are the stable key fields used by `mfs cat --locator`.
- `metadata_fields` are preserved as side data for inspection or client-side
  filtering.

For how `text_fields`, `locator_fields`, and `metadata_fields` appear in search
results as `content`, `locator`, and `metadata.fields`, see
[Content Model](content-model.md).

Read [Connector Configuration Recipes](connector-config-recipes.md#objects-field-guide)
for the authoring workflow and the [Connector Reference](connector-reference.md)
for per-scheme path shapes before writing `[[objects]]`. Different backends
have different identifier casing, nested-field rules, and defaults.

## HTTP API map

The CLI is a thin client over the `/v1` control plane. API users can use the
same lifecycle directly:

| Endpoint | CLI path | Request and response notes |
|---|---|---|
| `POST /v1/add` | `mfs add`, `mfs connector update` | `AddRequest` requires `target` and may include `config`, `full`, `since`, `process`, and `update`. The response returns `job_id`. |
| `POST /v1/connectors/probe` | `mfs connector probe` | Accepts `target` and optional `config`; returns `target`, `type`, `ok`, and `detail`. |
| `POST /v1/connectors/estimate` | Automatic from `mfs add` for external targets unless `--yes` is set | Accepts the same `target` and optional `config` shape as probe; returns object, chunk, and token estimates. |
| `GET /v1/connectors/inspect?target=...` | `mfs connector inspect` | Returns a JSON summary for the requested connector. |
| `DELETE /v1/connectors?target=...` | `mfs connector remove`, `mfs remove` | Removes a registered connector root. Child paths and unregistered targets return an error. |

## Search and browse after ingest

Once a connector is registered and indexed, use the same commands for every
scheme:

```bash
mfs search "billing escalation" zendesk://acme --top-k 10
mfs search "rate limit retry policy" --all
mfs ls github://owner/repo
mfs tree postgres://prod-db -L 3
mfs cat github://owner/repo/src/main.rs --range 100:150
mfs cat postgres://prod-db/public/tickets/rows.jsonl --locator '{"id": 12345}'
```

Use [Search and Browse](search-and-browse.md) for the general search loop. Use
[Connector Configuration Recipes](connector-config-recipes.md) when you need to
write or validate TOML after choosing a scheme. Use the
[Connector Reference](connector-reference.md) when you need URI tree shapes,
locator JSON examples, chunk kinds, and common pitfalls. Use
[Auth and Secrets](auth-and-secrets.md#connector-credentials) for the
server-side credential reference boundary. Use [Error Codes](errors.md) when
connector, upload, or sync failures return a canonical code.
