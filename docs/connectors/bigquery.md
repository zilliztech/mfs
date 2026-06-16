# BigQuery (`bigquery`)

The `bigquery` connector indexes BigQuery table rows as searchable records, with a
schema summary per table. Use it to search large analytical tables — a knowledge
base, an events table — by meaning.

## How MFS sees it

```text
bigquery://analytics/
└── events/
    └── tables/
        └── user_events/
            ├── rows.jsonl     table_rows    → one searchable chunk per row
            └── schema.json    table_schema  → searchable column summary
```

Rows are chunked per-row and need `[[objects]].text_fields` to become searchable.

## Credentials

BigQuery uses **Application Default Credentials (ADC)** — there is no token in the
TOML; the credentials must be visible to the **server process**. Three common
paths:

1. **Service account JSON** (production): create a service account with
   `roles/bigquery.dataViewer` on the target datasets, then point the server at
   it:

    ```bash
    export GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa.json
    mfs-server run
    ```

2. **`gcloud auth application-default login`** (dev / single-user) — writes
   `~/.config/gcloud/application_default_credentials.json`.

3. **Workload Identity** on GKE / Cloud Run — ADC is automatic.

Make sure the **BigQuery API** is enabled on the project.

## Configuration

```toml
project = "analytics-prod"
datasets = ["events", "kb"]
max_read_rows = 100000

[[objects]]
match = "/kb/tables/articles"
text_fields = ["title", "body_markdown"]
locator_fields = ["article_id"]
```

## Sync and freshness

The connector uses the table's `modified` time as its cursor; deletions are caught
by `full_scan`. It reads rows via `list_rows`, so `max_read_rows` caps large
tables.

## Search and browse

```bash
mfs add bigquery://analytics --config ./bigquery.toml

mfs search "refund event" bigquery://analytics/events/tables/user_events/rows.jsonl
mfs search "email column" bigquery://analytics --kind schema_summary
mfs cat bigquery://analytics/kb/tables/articles/rows.jsonl --locator '{"article_id":"a-123"}'
```

## Pitfalls

- ADC must be visible to the **server** process, not just the CLI shell.
- BigQuery has no primary key for most tables — choose stable `locator_fields`
  explicitly.
- Rows need `text_fields` to be searchable.
