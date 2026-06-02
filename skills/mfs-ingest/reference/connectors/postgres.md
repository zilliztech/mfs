# postgres connector — ingest

URI: `postgres://<alias>` (alias is free-form: `prod-db`, `analytics`, …).

## How to obtain credentials

You need a **DSN** in the form `postgresql://user:pass@host:5432/dbname`.

- **AWS RDS / Aurora / GCP Cloud SQL / Azure Postgres**: copy the connection
  string from the cloud console. Replace any `{{password}}` placeholder
  with the real password (or use IAM auth → still produces a DSN).
- **Self-hosted**: `\conninfo` inside `psql` shows the components; assemble
  into a DSN.
- **Docker compose**: the DSN your other service uses works here too.

**Probe it before MFS sees it**:
```bash
psql "$DSN" -c "SELECT 1"           # 1 returned → connectivity OK
psql "$DSN" -c "\dt"                # tables visible from this user
```

If `psql` fails, MFS will fail in the same way — fix at the source first.

## Required scopes / role

Create a read-only role; MFS only reads:

```sql
CREATE ROLE mfs_reader LOGIN PASSWORD 'xxx';
GRANT CONNECT ON DATABASE prod TO mfs_reader;
GRANT USAGE ON SCHEMA public TO mfs_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO mfs_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT ON TABLES TO mfs_reader;
```

The DSN should use this role, not your admin user.

## Required toml fields

| key | what |
|---|---|
| `dsn` | the connection string (use `env:PG_DSN` to keep it out of the file) |

## Optional toml fields

| key | default | meaning |
|---|---|---|
| `schemas` | `["public"]` | which schemas to enumerate |
| `cursor_column` | _none_ | column used for incremental sync (typically `updated_at`); if set, re-syncs only pull rows with `cursor > last_seen` |
| `max_read_rows` | 100000 | per-table cap (honoured mid-page) |

## `[[objects]]` blocks — per-table config

Postgres tables are NOT auto-indexed with sensible defaults; each table
the user wants searchable needs an `[[objects]]` block declaring which
columns become content.

```toml
[[objects]]
match = "public.tickets"
text_fields = ["title", "description", "tags"]
locator_fields = ["id"]
metadata_fields = ["status", "priority", "assignee", "updated_at"]
```

- `match`: `<schema>.<table>` or `<schema>.<table>:<filter>` to limit
  rows. Use double-quoted identifiers if any contain special chars.
- `text_fields`: which columns get joined into the embedded text. Pick
  the prose-like columns — `description`, `body`, `notes`, `title`.
  Don't include `id`, timestamps, or enum-like short codes.
- `locator_fields`: the primary key columns. `["id"]` for single-PK
  tables, `["org_id", "user_id"]` for composite. Used to reopen a row
  via `mfs cat --locator`.
- `metadata_fields`: side-data preserved in chunk metadata; users can
  filter searches on these.

Multiple `[[objects]]` blocks for multiple tables:

```toml
[[objects]]
match = "public.tickets"
text_fields = ["description"]
locator_fields = ["id"]

[[objects]]
match = "public.kb_articles"
text_fields = ["title", "body_markdown"]
locator_fields = ["id"]
```

## env: example

```toml
# in /tmp/mfs-pg-prod.toml
dsn = "env:PG_PROD_DSN"
schemas = ["public", "reporting"]
cursor_column = "updated_at"
max_read_rows = 500000

[[objects]]
match = "public.tickets"
text_fields = ["title", "description"]
locator_fields = ["id"]
```

```bash
export PG_PROD_DSN='postgresql://mfs_reader:...@db.example.com:5432/prod'
mfs add postgres://prod --config /tmp/mfs-pg-prod.toml
```

## Common pitfalls

- **Missing `[[objects]]` → sync succeeds with 0 chunks.** Without a
  text_fields declaration, MFS doesn't know what to embed; tables enumerate
  but never produce searchable content.
- **JSONB columns**: use `text_fields = ["data->>'description'"]` to dig
  one level in.
- **Big text columns + small `chunk_max`**: a single huge row's content
  gets capped at `chunk_max` chunks. Default 1M is rarely a problem; lower
  it only if one table dominates ingest spend.
- **pg_hba.conf blocking the source IP**: connection refused even though
  the DSN is correct. Tell user to check pg_hba on the DB host.
- **SSL required**: append `?sslmode=require` to the DSN.
- **High-cardinality `cursor_column`**: works fine. Low-cardinality
  cursors (rounded-to-day timestamps) can leave gaps; pick a per-row
  unique column.
