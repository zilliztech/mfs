# postgres connector (`postgres://`)

## What this is

Standard PostgreSQL (or any wire-compatible — RDS / Aurora / Supabase / Neon /
CrunchyBridge). The connector talks to one database via `asyncpg` (native async,
fast). Each schema's tables become a virtual filesystem tree.

**When MFS helps**: large operational DBs with many tables you want to search
through semantically — tickets, audit logs, product catalogs — without writing
SQL across them.

**Cost note**: MFS hits Postgres during sync and on `head` / `cat --range` /
`cat --locator` / `grep`. `search` runs purely against the Milvus index, never
touches Postgres.

## URI shape

```
/                                          (root)
/public/                                    schema
/public/tickets/                            table folder
/public/tickets/rows.jsonl                  table rows (LAZY)
/public/tickets/schema.json                 column list (eager)
```

Column / table / schema names are returned **lowercase** (Postgres's default
identifier folding, the opposite of Snowflake).

Optional `schemas = ["public", "ops"]` scopes the scan; otherwise all
non-system schemas are enumerated.

## Auth

Connection string only. The password and any other secret material live in
the DSN itself:

```toml
credential_ref = "env:PG_DSN"
# resolves to e.g. "postgresql://mfs:hunter2@db.internal:5432/prod"
```

DSN format: `postgresql://<user>:<pass>@<host>:<port>/<dbname>[?options]`.
Standard options that work: `sslmode=require`, `application_name=mfs`.

## Connector config TOML

```toml
# ─── connection (required) ───
credential_ref = "env:PG_DSN"   # full Postgres DSN

# ─── optional ───
# schemas = ["public", "ops"]              # restrict to these schemas; default = all non-system
# max_read_rows = 200000                   # LIMIT per table read; default 100000
# cursor_column = "updated_at"             # incremental cursor; auto-detected from
                                           # ["updated_at","modified_at","last_modified",
                                           #  "updated","modified","mtime"] (case-insensitive)

# ─── per-table field mapping ───
[[objects]]
match           = "/public/tickets/rows.jsonl"   # or "*rows.jsonl" for all tables
text_fields     = ["subject", "body"]    # embedding content (lowercase — Postgres default)
locator_fields  = ["id"]                 # primary key for `cat --locator`
metadata_fields = ["status", "priority"] # filterable metadata
# indexable = true                       # default; false = keep ls/cat but don't index
# chunk_max = 1_000_000                  # safety cap
```

### Field-mapping rules

- **`text_fields` required for searchability.** Empty → 0 chunks indexed,
  no warning.
- Names are **lowercase** unless the table was created with double-quoted
  uppercase identifiers (rare).
- JSONPath-lite supported (`a.b`, `a[*].b`) — useful when a column is `jsonb`.
- Multiple `[[objects]]` blocks: first match wins; specific patterns before
  catch-alls.
- Chunking is per-row; the user does not choose.

## What each command does

| Command | Behaviour |
|---|---|
| `mfs ls /` | lists schemas. |
| `mfs ls /<schema>/` | lists tables via `information_schema.tables`. |
| `mfs cat /<schema>/<tbl>/rows.jsonl` | **refused** (lazy). Use `head`, `--range`, or `--locator`. |
| `mfs cat .../rows.jsonl --range A:B` | `SELECT * FROM <tbl> LIMIT (B-A) OFFSET A`. |
| `mfs cat .../rows.jsonl --locator '{"id":1}'` | `SELECT * FROM <tbl> WHERE id = 1`. |
| `mfs head -n N .../rows.jsonl` | `SELECT * FROM <tbl> LIMIT N` (uses `head_cache` artifact on repeat runs). |
| `mfs cat .../schema.json` | one-shot column list (name + type). |
| `mfs grep PATTERN .../rows.jsonl --field subject` | **pushed down** to `SELECT ... WHERE subject ILIKE '%PATTERN%'`. Case-insensitive, literal-exact (no regex). Without `--field` falls back to per-row scan. |
| `mfs search "QUERY"` | Milvus only. Returns `row_text` chunks (per-row content from `text_fields`) + `schema_summary` if `summary.enabled` in server.toml. |

## Typical workflow

```bash
# 1. (One-time) DB-side: create a narrow read-only role for MFS.
# psql -U postgres -d prod
#   CREATE ROLE mfs_ro LOGIN PASSWORD '...';
#   GRANT CONNECT ON DATABASE prod TO mfs_ro;
#   GRANT USAGE ON SCHEMA public TO mfs_ro;
#   GRANT SELECT ON ALL TABLES IN SCHEMA public TO mfs_ro;
#   ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO mfs_ro;

# 2. Export DSN to the MFS server environment.
export PG_DSN="postgresql://mfs_ro:...@db.internal:5432/prod?sslmode=require"

# 3. Register.
mfs add postgres://prod --config postgres-prod.toml

# 4. Search → reopen exact row.
mfs search "SSO bounced identity provider" --connector-uri postgres://prod
mfs cat postgres://prod/public/tickets/rows.jsonl --locator '{"id":9001}'

# 5. Incremental refresh after upstream mutations.
mfs add postgres://prod --no-full
```

## Incremental sync

Per-table fingerprint = `count(*) | max(cursor_column)`. INSERTs change count;
UPDATEs change `max(cursor_column)` only if a cursor column was detected and
the application updates it. Add a trigger if you want true in-place-update
detection:

```sql
CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS trigger AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END $$ LANGUAGE plpgsql;
CREATE TRIGGER tickets_touch BEFORE UPDATE ON tickets
  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
```

DDL drift (column added/dropped/typed) → captured by `schema.json` fingerprint
(`schema:col:type;…`) → triggers `schema_summary` re-build.

## Gotchas

1. **`text_fields` required, lowercase.** Default Postgres folds identifiers
   to lowercase; quoted mixed-case names are rare but valid — match what's
   actually in the column dump.
2. **`jsonb` columns** — access nested fields with JSONPath-lite
   (`payload.user.email`); the connector returns `jsonb` as a Python dict so
   nested access just works.
3. **`max_read_rows` truncation** marks the object `partial` — see
   `mfs connector status`.
4. **`indexable = false` still queries Postgres** on `ls`/`cat`/`head` —
   it only skips embedding. To exclude a table entirely, narrow `schemas`
   or revoke SELECT on it for the MFS role.
5. **No row-level filter at config time** (was `index_filter`, removed in
   v0.4 for security). Filter at the source with a `VIEW`:
   ```sql
   CREATE VIEW open_tickets AS SELECT * FROM tickets WHERE status = 'open';
   ```
   then map `[[objects]]` against `/public/open_tickets/rows.jsonl`.
6. **SSL**: production Postgres usually requires `sslmode=require` or
   `verify-full`. Put it in the DSN; asyncpg honours it.
