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

1. **Service account JSON** (production): in Google Cloud Console, open
   *IAM & Admin → Service Accounts* → **Create service account**. Name it
   `mfs-bigquery-reader`, grant `roles/bigquery.dataViewer` on the target
   datasets or project, then open the service account's *Keys* tab →
   **Add key → Create new key → JSON**. Store the downloaded JSON outside the
   repo and point the server at it:

    ```bash
    export GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa.json
    mfs-server run
    ```

2. **`gcloud auth application-default login`** (dev / single-user): run it as
   the same OS user that starts `mfs-server`. It opens a browser consent flow and
   writes `~/.config/gcloud/application_default_credentials.json`.

3. **Workload Identity** on GKE / Cloud Run — ADC is automatic.

Before any of those, open *APIs & Services → Library → BigQuery API* and enable
it on the project. If the connector can authenticate but cannot list a dataset,
check the dataset IAM page first; the service account needs read access to every
dataset listed in `datasets`.

![Google Cloud BigQuery API page](https://github.com/user-attachments/assets/fd034ea0-4607-4e74-b65a-106460830889)

![Google Cloud service accounts page](https://github.com/user-attachments/assets/6d7dfb43-f7f9-46dc-a049-ed9cc251afea)

![Google Cloud create service account form](https://github.com/user-attachments/assets/d8275bc9-1792-44fd-951a-18dd3cc789fd)

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

Save the file as `bigquery.toml`, then probe before the first index:

```bash
mfs connector probe bigquery://analytics --config ./bigquery.toml
mfs add bigquery://analytics --config ./bigquery.toml
```

## Sync and freshness

The connector uses the table's `modified` time as its cursor; deletions are caught
by `full_scan`. It reads rows via `list_rows`, so `max_read_rows` caps large
tables.

## Search and browse

```bash
mfs search "refund event" bigquery://analytics/events/tables/user_events/rows.jsonl
mfs search "email column" bigquery://analytics --kind schema_summary
mfs cat bigquery://analytics/kb/tables/articles/rows.jsonl --locator '{"article_id":"a-123"}'
```

## Pitfalls

- ADC must be visible to the **server** process, not just the CLI shell.
- BigQuery has no primary key for most tables — choose stable `locator_fields`
  explicitly.
- Rows need `text_fields` to be searchable.
- User ADC from `gcloud` is convenient for local testing; service accounts or
  workload identity are easier to operate in long-running deployments.
