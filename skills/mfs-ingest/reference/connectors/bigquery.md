# bigquery connector — ingest

URI: `bigquery://<alias>`.

## How to obtain credentials

BigQuery uses **Application Default Credentials (ADC)**, NOT a token in
the connector toml.

Three ways to provide credentials to the server process:

1. **Service account JSON file** — most common in production:
   ```bash
   export GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa.json
   # then start the server
   mfs-server run
   ```
2. **`gcloud auth application-default login`** — for dev / single-user
   machines. Creates `~/.config/gcloud/application_default_credentials.json`.
3. **Workload Identity** — on GKE / Cloud Run, ADC is automatic.

The connector toml carries the project + dataset list only.

## Required scopes / IAM

Grant the service account (or user) these roles on the project:
- `roles/bigquery.dataViewer` — read tables
- `roles/bigquery.jobUser` — required for `list_rows` API calls

For specific datasets only, you can grant `dataViewer` per dataset
instead of project-wide.

## Required toml fields

| key | what |
|---|---|
| `project` | GCP project ID (e.g. `analytics-prod-1234`) |
| `datasets` | comma-separated list of datasets to enumerate (e.g. `["events", "warehouse"]`) |

## Optional

| key | meaning |
|---|---|
| `endpoint` | custom endpoint (BigQuery emulator URL for local dev) |
| `max_read_rows` | per-table cap |

## `[[objects]]` blocks

```toml
[[objects]]
match = "events.user_events"
text_fields = ["event_name", "event_properties_json"]
locator_fields = ["event_id"]
```

## env: example

```toml
project = "analytics-prod-1234"
datasets = ["events", "kb"]
max_read_rows = 1000000

[[objects]]
match = "kb.articles"
text_fields = ["title", "body_markdown"]
locator_fields = ["article_id"]
```

```bash
export GOOGLE_APPLICATION_CREDENTIALS=/etc/mfs/bq-sa.json
mfs add bigquery://analytics --config /tmp/mfs-bq.toml
```

## Pitfalls

- **ADC env var on the SERVER**: the toml doesn't carry credentials.
  `GOOGLE_APPLICATION_CREDENTIALS` must be set in the server's env
  (where `mfs-server run` is running), NOT the user's shell where
  `mfs add` runs.
- **`list_rows` quota**: BigQuery rate-limits the tabledata.list API.
  Big tables (>1M rows) ingest slowly; consider `max_read_rows` cap.
- **No `cursor_column` support**: this connector full-scans on every
  sync (no incremental cursor). For large datasets, expect re-syncs
  to take a while.
