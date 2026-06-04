# Connector Configuration Recipes

Use this page after you have chosen a connector scheme and need to write a
safe, runnable TOML file. It is an authoring workflow layer:

- use [Connectors](connectors.md) to choose a source and manage lifecycle;
- use [Connector Reference](connector-reference.md) for per-scheme fields,
  URI trees, locators, and pitfalls;
- use [Auth and Secrets](auth-and-secrets.md#connector-credentials) for the
  server-side credential boundary;
- use [Content Model](content-model.md) for result envelopes, chunk kinds,
  `locator`, and `metadata.fields`.

## Choose the Recipe

| Source shape | Common schemes | Start with | Usually needs `[[objects]]`? | First readback |
|---|---|---|---|---|
| Website or rendered document tree | `web`, `gdrive`, document-heavy `s3`, repository files in `github` | Scope the crawl, folder, prefix, or repo path first. | No for normal document/code paths. | `mfs ls TARGET`, then `mfs cat OBJECT --range 1:80`. |
| Object-key or file tree | `file`, `s3` | Keep the root/prefix narrow, then add only object rules for search exclusions or caps. | No unless you need `indexable = false`, `chunk_max`, or other path-specific rules. | `mfs tree TARGET -L 2` and a bounded `cat`. |
| SQL table rows | `postgres`, `mysql`, `bigquery`, `snowflake` | Pick tables, text columns, stable locator columns, and metadata columns. | Yes for searchable row chunks. | `mfs head .../rows.jsonl -n 5`, then `cat --locator`. |
| Document records | `mongo`, Notion data source records, CRM/support objects | Pick collection/object paths and record fields that contain prose. | Usually yes unless the scheme reference documents a built-in preset for that path. | `mfs head` on the scheme-specific JSONL path, then `cat --locator`. |
| Messages and threads | `slack`, `discord`, `gmail`, `feishu` | Use built-in message presets first, then narrow scope with connector-specific fields. | Usually no for message streams covered by presets. | `mfs ls .../channels`, `mfs search`, then `cat --locator`. |

Do not infer connector behavior from another scheme. If the reference says a
scheme has a built-in preset, start without `[[objects]]`. If the reference says
rows, data source records, or CRM records need text fields, add explicit
`[[objects]]` blocks before syncing.

## Credential Matrix

Connector config is TOML loaded by the CLI and sent to the API as `config`.
Supported credential references are resolved by the server/plugin process when
the connector is built, not by the client shell that runs `mfs`.

| Form | Example | Resolved by | Use when | Check first |
|---|---|---|---|---|
| Top-level `env:VAR` | `token = "env:SLACK_BOT_TOKEN"` | Server process environment | Tokens, DSNs, API keys, short secrets. | The variable must be set where `mfs-server` runs. |
| Top-level `file:/abs/path` | `credential_ref = "file:/etc/mfs/snowflake/key.p8"` | Server process filesystem | Mounted secrets, PEM keys, multiline credentials. | The path must be absolute and readable by the server container, host, or pod. |
| `credential_ref` fallback | `credential_ref = "env:PG_DSN"` | Server process, then plugin constructor | Connectors that explicitly use the plugin credential fallback, such as Snowflake private keys or DSN/token fallbacks. | Confirm the scheme reference names `credential_ref` or supports credential fallback. |
| Process-native environment | `GITHUB_TOKEN`, `GOOGLE_APPLICATION_CREDENTIALS` | Connector plugin or provider SDK | Connectors whose code reads a well-known process env var directly. | Set it on the server process and probe. Do not add invented TOML fields. |
| Plaintext string | `token = "xoxb-..."` | No indirection | Short demos only. | It can remain in local TOML, backups, shell history, or operator copies before any server-side redaction. |

!!! warning "Top-level means top-level"
    The generic resolver only rewrites top-level string values in the connector
    config. A nested OAuth token object is passed as data unless that connector
    implements its own parsing. Keep nested credential objects out of public
    examples unless the scheme reference explicitly documents them.

## `[[objects]]` Field Guide

`[[objects]]` blocks are per-object rules. User rules are matched before a
connector's built-in preset. Use connector-relative paths from
[Connector Reference](connector-reference.md), `mfs ls`, or `mfs head` rather
than guessing.

```toml
[[objects]]
match = "/public/tickets"
text_fields = ["title", "description", "comments[].body"]
locator_fields = ["id"]
metadata_fields = ["status", "priority", "updated_at"]
chunk_max = 50000
```

| Field | Use it for | Authoring rule |
|---|---|---|
| `match` | Select one connector-relative path or path family. | Prefer the shortest stable path that selects the intended object, such as `/public/tickets` for `/public/tickets/rows.jsonl`. |
| `text_fields` | Produce searchable `row_text` or `thread_aggregate` content. | Choose prose-like fields: title, body, description, comments, notes, markdown. Include nested arrays only when the connector record shape supports them, such as `comments[].body`. |
| `locator_fields` | Build the JSON locator used by `mfs cat --locator`. | Use stable primary keys or source IDs. Never use `lines`; that key is reserved for text/code/document range locators. |
| `metadata_fields` | Copy side data into `metadata.fields` on search hits. | Use status, priority, owner, timestamps, labels, or IDs that help clients filter or explain results. |
| `indexable` | Keep a path browseable but not searchable. | Set `indexable = false` for noisy generated files, binary-like paths, or records you only want to browse. |
| `chunk_max` | Cap chunks for one object. | Lower it only to contain large files, runaway rows, or long threads; capped objects can show partial recall. |
| `group_by` | Override message/thread grouping. | Use only for message-like records when the default thread key is wrong. |
| `render_template` | Override rendered text for each record. | Keep it simple and verify with search JSON plus readback; missing fields render as empty strings. |

If `locator_fields` is omitted for structured records, the engine can fall back
to a row-offset locator. Prefer explicit source keys instead; row offsets are
not a durable identity for mutable sources.

## Recipe Tabs

=== "Website Crawl"

    Use this when the source is a bounded HTTP documentation site. The web
    connector fetches HTTP responses and exposes converted markdown under
    `pages/`.

    ```toml
    start_urls = ["https://docs.example.com/"]
    allowed_domains = ["docs.example.com"]
    max_pages = 100
    ```

    ```bash
    mfs connector probe web://docs --config ./web.toml
    mfs add web://docs --config ./web.toml --wait

    mfs ls web://docs/pages/docs.example.com
    mfs search "installation" web://docs
    mfs cat web://docs/pages/docs.example.com/index.md --range 1:80
    ```

    Keep `allowed_domains` narrow. Raise `max_pages` only after confirming the
    tree shape with `mfs ls` or `mfs tree`.

=== "Object or File Tree"

    Use this when the source is an S3-compatible bucket/prefix or a file tree
    where document/code/image object kinds are enough.

    ```toml
    bucket = "acme-docs"
    prefix = "engineering/rfc/"
    region = "us-west-2"
    access_key_id = "env:AWS_ACCESS_KEY_ID"
    secret_access_key = "env:AWS_SECRET_ACCESS_KEY"
    ```

    ```bash
    mfs connector probe s3://acme-docs --config ./s3.toml
    mfs add s3://acme-docs --config ./s3.toml --wait

    mfs tree s3://acme-docs -L 2
    mfs search "retention policy" s3://acme-docs/engineering/rfc/
    mfs cat s3://acme-docs/engineering/rfc/rfc-001.md --range 1:80
    ```

    Add `endpoint_url` for R2, GCS S3 interop, MinIO, or another compatible
    endpoint. Keep `prefix` exact; include a trailing slash when you mean a
    directory-like prefix.

=== "SQL Rows"

    Use this when each table row should become one searchable record. The
    example uses Postgres; apply the same `[[objects]]` discipline to MySQL,
    BigQuery, and Snowflake, while keeping each scheme's path casing and fields
    from the reference.

    ```toml
    dsn = "env:PG_PROD_DSN"
    schemas = ["public"]
    cursor_column = "updated_at"
    max_read_rows = 100000

    [[objects]]
    match = "/public/tickets"
    text_fields = ["title", "description"]
    locator_fields = ["id"]
    metadata_fields = ["status", "priority", "updated_at"]
    ```

    ```bash
    mfs connector probe postgres://prod-db --config ./postgres.toml
    mfs add postgres://prod-db --config ./postgres.toml --wait

    mfs head postgres://prod-db/public/tickets/rows.jsonl -n 5
    mfs search "SSO migration" postgres://prod-db/public/tickets/rows.jsonl
    mfs cat postgres://prod-db/public/tickets/rows.jsonl --locator '{"id":12345}'
    mfs search "email column" postgres://prod-db --kind schema_summary
    ```

    Missing `text_fields` means rows can be browseable but may produce no
    searchable row chunks. Use read-only database credentials.

=== "Document Records"

    Use this for records that are not SQL rows but still need per-record text
    and locators. The example uses MongoDB documents.

    ```toml
    uri = "env:MONGO_URI"
    database = "support"
    cursor_field = "updatedAt"
    max_read_docs = 50000

    [[objects]]
    match = "/support_threads"
    text_fields = ["title", "messages[].body"]
    locator_fields = ["_id"]
    metadata_fields = ["status", "updatedAt"]
    ```

    ```bash
    mfs connector probe mongo://prod-cluster --config ./mongo.toml
    mfs add mongo://prod-cluster --config ./mongo.toml --wait

    mfs head mongo://prod-cluster/support_threads/documents.jsonl -n 5
    mfs search "refund escalation" mongo://prod-cluster/support_threads/documents.jsonl
    mfs cat mongo://prod-cluster/support_threads/documents.jsonl --locator '{"_id":"65a3..."}'
    ```

    Heterogeneous documents can miss fields. Start with `head`, choose fields
    that actually appear, then probe and sync.

=== "Messages With Presets"

    Use built-in message presets before writing custom `[[objects]]`. The
    example uses Slack: messages are grouped by thread and workspace users are
    indexed separately.

    ```toml
    token = "env:SLACK_BOT_TOKEN"
    max_read_rows = 50000
    ```

    ```bash
    mfs connector probe slack://acme --config ./slack.toml
    mfs add slack://acme --config ./slack.toml --wait

    mfs ls slack://acme/channels
    mfs search "deploy failed" slack://acme
    mfs cat slack://acme/channels/eng-backend__C012345/messages.jsonl \
      --locator '{"thread_ts":"1717123456.001200"}'
    mfs search "Alice Wang" slack://acme/users.jsonl
    ```

    If private channels are in scope, the bot must have the required Slack
    scopes and be a member of those private channels.

## Probe, Add, Update Checklist

1. Confirm the client points at the intended server.

   ```bash
   mfs status
   mfs config show
   ```

2. Confirm credentials are visible to the server process, not only the CLI
   shell. For Docker or Kubernetes, check inside the container or pod.

3. Probe the exact target and TOML before registering.

   ```bash
   mfs connector probe TARGET --config ./connector.toml
   ```

4. Add and wait for a first sync when you want an immediate pass/fail result.

   ```bash
   mfs add TARGET --config ./connector.toml --wait
   ```

   For external targets, the CLI runs a pre-flight estimate and asks for
   confirmation unless `--yes` is set. Use `--yes` only after you already
   accept the object/chunk/token estimate.

5. Update config on an existing connector with `connector update`, not a
   normal re-add. The server ignores `--config` on an already registered
   connector unless the request is marked as an update.

   ```bash
   mfs connector update TARGET --config ./connector.toml
   ```

6. Re-sync existing content without changing config through `mfs add TARGET`.
   Use `--since` only for connectors with a supported cursor kind; unsupported schemes
   return `since_unsupported`.

   ```bash
   mfs add TARGET --wait
   mfs add TARGET --since 2026-06-01 --wait
   mfs add TARGET --force-index --wait
   ```

7. Inspect the connector and the job before changing fields again.

   ```bash
   mfs connector list
   mfs connector inspect TARGET
   mfs job list
   mfs job show JOB_ID
   ```

## Readback Validation

Search proves only that a candidate chunk matched. Validate the object, locator,
and rendered fields before relying on the result.

```bash
mfs ls TARGET --json
mfs --json search "billing escalation" TARGET --top-k 5
```

Structured result shape:

```json
{
  "source": "postgres://prod-db/public/tickets/rows.jsonl",
  "content": "title: Login broken after SSO migration\nstatus: open",
  "score": 0.84,
  "locator": {"id": 12345},
  "metadata": {
    "chunk_kind": "row_text",
    "fields": {"status": "open", "priority": "high"}
  }
}
```

Reopen it:

```bash
mfs cat postgres://prod-db/public/tickets/rows.jsonl --locator '{"id":12345}'
```

Text/code/document result shape:

```json
{
  "source": "github://zilliztech/mfs/server/python/src/mfs_server/connectors/base.py",
  "locator": {"lines": [209, 240]},
  "metadata": {"chunk_kind": "body"}
}
```

Reopen it:

```bash
mfs cat github://zilliztech/mfs/server/python/src/mfs_server/connectors/base.py --range 209:240
mfs cat github://zilliztech/mfs/server/python/src/mfs_server/connectors/base.py --locator '{"lines":[209,240]}'
```

If `mfs ls PATH --json` shows `not_indexed`, use browse/read commands and fix
the object rule if it should be searchable. If it shows `partial`, search can
still return useful hits, but recall may be incomplete; narrow the source,
raise the relevant cap, or adjust `chunk_max` after verifying the cause.
