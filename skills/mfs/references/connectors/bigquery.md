# bigquery connector (`bigquery://`)

## What this is

Google BigQuery data warehouse. The connector uses the official
`google-cloud-bigquery` client (sync, wrapped in `asyncio.to_thread`) to
enumerate datasets / tables and stream row data via `query` /
`list_rows`. Layout mirrors snowflake's:
`/<dataset>/tables/<table>/{rows.jsonl, schema.json}`.

**Critical cost note**: BigQuery bills **per byte scanned**. A naive
`SELECT *` on a wide / huge table can be expensive. MFS guards against this
via `max_read_rows` (LIMIT-only scans, no full-table read) and never repeats
a scan if the table fingerprint hasn't changed. Still — **scope your
connector to specific datasets** and keep `max_read_rows` realistic.

**When MFS helps**: BigQuery houses semi-structured operational data (event
streams, logs, raw imports) that you want searchable without writing SQL
across every dataset.

## URI shape

```
/                                            (root: configured datasets)
/analytics/                                  dataset
/analytics/tables/                           folder
/analytics/tables/events/                    table folder
/analytics/tables/events/rows.jsonl          rows (LAZY)
/analytics/tables/events/schema.json         column list (eager)
```

Column / table / dataset names are case-preserving in BigQuery (unlike
Snowflake or Postgres), so `text_fields` should match the source casing
exactly.

## Auth

Service-account JSON key, located on the server filesystem:

```toml
project = "my-gcp-project"
credential_ref = "file:/var/run/secrets/gcp/bigquery-sa.json"
```

The connector loads the JSON, builds a `google.oauth2.service_account.Credentials`
under the hood, and the client uses it for all calls. The service account
needs at minimum:
- `BigQuery Data Viewer` on the target datasets
- `BigQuery Job User` on the project (to run query jobs)

**Emulator note**: for tests / dev, set `endpoint = "http://localhost:9050"`
to point at `goccy/bigquery-emulator`; the connector skips auth in that case.

## Connector config TOML

```toml
# ─── connection (required) ───
project = "my-gcp-project"
credential_ref = "file:/var/run/secrets/gcp/bigquery-sa.json"

# ─── optional ───
# datasets = ["analytics", "raw"]          # restrict scope; default = all datasets in project
# endpoint = "http://localhost:9050"       # emulator only — skips auth
# max_read_rows = 100000                   # LIMIT per table scan; default 100000
# host = "..."                              # rarely needed; for custom BigQuery endpoints

# ─── per-table field mapping ───
[[objects]]
match           = "/analytics/tables/events/rows.jsonl"
text_fields     = ["event_name", "payload.message"]   # JSONPath-lite into nested RECORDs
locator_fields  = ["event_id"]
metadata_fields = ["user_id", "timestamp"]
# indexable = true
# chunk_max = 1_000_000
```

## What each command does

| Command | Behaviour |
|---|---|
| `mfs ls /` | `client.list_datasets()` filtered by `datasets` config. |
| `mfs ls /<dataset>/tables/` | `client.list_tables(dataset)`. |
| `mfs cat /<dataset>/tables/<tbl>/rows.jsonl` | **refused** (lazy + expensive). |
| `mfs cat .../rows.jsonl --range A:B` | `client.list_rows(table, start_index=A, max_results=B-A)` — bounded read, no query job. |
| `mfs cat .../rows.jsonl --locator '{"event_id":"..."}'` | parameterized `SELECT * WHERE event_id = @v` (one query job). |
| `mfs head -n N .../rows.jsonl` | `client.list_rows(table, max_results=N)`. `head_cache` reused. |
| `mfs cat .../schema.json` | `client.get_table(ref).schema` — free metadata call. |
| `mfs grep PATTERN .../rows.jsonl --field event_name` | parameterized `SELECT * WHERE event_name LIKE @p` (query job — billed). |
| `mfs search "QUERY"` | Milvus only. `row_text` chunks per row. |

## Typical workflow

```bash
# 1. GCP-side: create a service account with BigQuery Data Viewer on the target
#    datasets + BigQuery Job User on the project. Download its JSON key.

# 2. Put the JSON key on the MFS server host (k8s Secret / docker secret mount).
#    chmod 600 /var/run/secrets/gcp/bigquery-sa.json

# 3. Register.
mfs add bigquery://prod --config bigquery-prod.toml

# 4. Use.
mfs search "ssl handshake failure" --connector-uri bigquery://prod
mfs cat bigquery://prod/analytics/tables/events/rows.jsonl --locator '{"event_id":"abc-123"}'

# 5. Incremental refresh.
mfs add bigquery://prod --no-full
```

## Incremental sync

Per-table fingerprint = `num_rows | last_modified_time` from the table
metadata (both free — no query job needed). Any change to the table triggers
a re-scan capped at `max_read_rows`. **Streaming-buffer inserts** can lag in
`last_modified_time` — for streaming tables consider lowering the fingerprint
to `num_rows` only by deleting the metadata's `last_modified_time` from the
cache (advanced).

## Gotchas

1. **Cost discipline**: every `grep` / `cat --locator` runs a query job.
   `mfs head` and `cat --range` are cheaper (use `list_rows`, no query job).
   Whenever possible, drive the workflow with `search` (free, against Milvus)
   and only `cat --locator` exact hits.
2. **`max_read_rows` is your only scan cap.** Wide tables × millions of
   rows × per-row embedding = real \$. Start with 10k-100k.
3. **Nested fields** (`RECORD`/`STRUCT`) are dict-like in the row dict; use
   `parent.child` syntax in `text_fields`. `REPEATED` fields use `field[*]`.
4. **Service-account JSON file is the only auth path.** ADC (default
   credentials from the gcloud CLI) is not supported by the connector — the
   server needs an explicit `credential_ref = "file:"` to a JSON.
5. **Emulator**: for unit/integration tests against `bigquery-emulator`,
   set `endpoint = "http://localhost:9050"` and omit `credential_ref`.
   The connector skips the service-account loader for the emulator path.
6. **`indexable = false` does NOT save BigQuery cost** — it skips Milvus
   embedding but `ls/head/cat --range` still call the API. To truly skip a
   table, narrow `datasets` or omit its `[[objects]]` match.
