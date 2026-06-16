# Postgres (`postgres`)

The `postgres` connector indexes table rows as searchable records. Each row
becomes an object you can search by meaning, and each table also gets a schema
summary so you can search the *structure* ("which table has an email column?")
not just the data.

## How MFS sees it

Tables live under their schema. Every table exposes its rows and its schema:

```text
postgres://prod-db/
└── public/
    ├── tickets/
    │   ├── rows.jsonl     table_rows    → one searchable chunk per row
    │   └── schema.json    table_schema  → searchable column summary
    └── users/
        ├── rows.jsonl
        └── schema.json
```

Rows are chunked per-row. To make a table searchable you must tell the connector
which columns carry text — see `[[objects]]` below. Without that, rows still
enumerate and you can `grep`/`cat` them, but they produce no semantic chunks.

## Credentials

You already have a database; what you need is a **DSN** and a read-only role.

```text
postgresql://user:pass@host:5432/dbname
```

- **Cloud Postgres** (RDS/Aurora, Cloud SQL, Azure): copy the connection string
  from the console; substitute the real password.
- **Self-hosted**: run `\conninfo` in `psql` to read host/port/db/user.

A read-only role is enough — `USAGE` on each in-scope schema plus `SELECT` on its
tables. Confirm connectivity from the machine that runs the server before handing
the DSN to MFS:

```bash
psql "$DSN" -c "SELECT 1"
psql "$DSN" -c "\dt"
```

## Configuration

```toml
dsn = "env:PG_DSN"
schemas = ["public"]
cursor_column = "updated_at"   # enables incremental re-sync
max_read_rows = 100000

[[objects]]
match = "/public/tickets"
text_fields = ["title", "description"]
locator_fields = ["id"]
metadata_fields = ["status", "updated_at"]
```

`match` targets the connector-relative object path (`/public/tickets`), which
covers its `rows.jsonl`. `text_fields` become the embedded text; `locator_fields`
let you reopen an exact row with `cat --locator`; `metadata_fields` are returned
alongside hits for filtering and display.

## Sync and freshness

When you set `cursor_column` (typically `updated_at`), the connector tracks the
high-water mark and re-syncs only rows changed since the last run. Deletions are
caught by `full_scan`. `grep` runs as a pushdown — it queries Postgres directly
rather than the index.

## Search and browse

```bash
mfs connector probe postgres://prod-db --config ./postgres.toml
mfs add postgres://prod-db --config ./postgres.toml

mfs search "SSO migration" postgres://prod-db/public/tickets/rows.jsonl
mfs search "email column" postgres://prod-db --kind schema_summary
mfs cat postgres://prod-db/public/tickets/rows.jsonl --locator '{"id":12345}'
```

## Pitfalls

- No `text_fields` → rows enumerate but produce no searchable chunks.
- Use read-only credentials; the connector only needs `SELECT`.
- `max_read_rows` caps large tables and can mark recall partial.
