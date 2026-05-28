# mysql connector (`mysql://`)

## What this is

MySQL / MariaDB / Aurora-MySQL via `aiomysql` (async). One **database** per
connector — the tables in that DB become a virtual filesystem tree.

Identical pipeline to **postgres** (same `table_rows` engine path, same
locator shape, same per-row chunking). The differences are: one DB per
connector (no schema layer in the URI), and SQL `LIKE` instead of `ILIKE` for
grep pushdown.

**When MFS helps**: same as postgres — searching across many tables
semantically without writing SQL.

## URI shape

```
/                                           (root: tables of the configured DB)
/tickets/                                   table folder
/tickets/rows.jsonl                         table rows (LAZY)
/tickets/schema.json                        column list (eager)
```

No schema layer — MySQL's "database" maps to a connector instance. To index
multiple databases, register multiple connectors (`mysql://prod-app`,
`mysql://prod-billing`, …).

## Auth

Standard MySQL credentials. `password` can be inline (dev) or via
`credential_ref` (prod — safer because the inline value is redacted before
persistence and won't survive a server restart):

```toml
host = "db.internal"
port = 3306
user = "mfs_ro"
database = "prod"
credential_ref = "env:MYSQL_PW"     # resolves to the password
```

## Connector config TOML

```toml
# ─── connection (required) ───
host     = "db.internal"
port     = 3306                  # optional, default 3306
user     = "mfs_ro"
database = "prod"
credential_ref = "env:MYSQL_PW"  # password via env or file:

# ─── optional ───
# max_read_rows = 200000                   # LIMIT per table; default 100000
# cursor_column = "updated_at"             # incremental cursor; auto-detected from
                                           # ["updated_at","modified_at","last_modified",
                                           #  "updated","modified","mtime"]

# ─── per-table field mapping ───
[[objects]]
match           = "/tickets/rows.jsonl"
text_fields     = ["subject", "body"]
locator_fields  = ["id"]
metadata_fields = ["status", "priority"]
# indexable = true
# chunk_max = 1_000_000
```

Field-mapping rules and JSONPath-lite syntax are identical to postgres — see
[postgres.md](postgres.md#field-mapping-rules).

## What each command does

| Command | Behaviour |
|---|---|
| `mfs ls /` | `SHOW TABLES` against the configured `database`. |
| `mfs cat /<tbl>/rows.jsonl` | **refused** (lazy). |
| `mfs cat .../rows.jsonl --range A:B` | `SELECT * FROM <tbl> LIMIT (B-A) OFFSET A`. |
| `mfs cat .../rows.jsonl --locator '{"id":1}'` | `SELECT * FROM <tbl> WHERE id = 1`. |
| `mfs head -n N .../rows.jsonl` | `SELECT * FROM <tbl> LIMIT N`. `head_cache` artifact reused on repeat runs. |
| `mfs cat .../schema.json` | column list from `information_schema.columns`. |
| `mfs grep PATTERN .../rows.jsonl --field subject` | **pushed down** to `SELECT ... WHERE subject LIKE '%PATTERN%'`. Literal only; case sensitivity depends on the column's collation (`utf8mb4_general_ci` is case-insensitive; `_bin` is sensitive). |
| `mfs search "QUERY"` | Milvus only. `row_text` chunks + `schema_summary` (when `summary.enabled`). |

## Typical workflow

```bash
# 1. DB-side: dedicated read-only user.
# mysql -uroot
#   CREATE USER 'mfs_ro'@'%' IDENTIFIED BY '...';
#   GRANT SELECT ON prod.* TO 'mfs_ro'@'%';

# 2. Server-side env.
export MYSQL_PW="..."

# 3. Register.
mfs add mysql://prod --config mysql-prod.toml

# 4. Use it.
mfs search "dark mode setting" --connector-uri mysql://prod
mfs cat mysql://prod/tickets/rows.jsonl --locator '{"id":42}'

# 5. Incremental refresh.
mfs add mysql://prod --no-full
```

## Incremental sync

Per-table fingerprint = `count(*) | max(cursor_column)` — same as postgres.
Add a trigger to drive UPDATE detection if the application doesn't already
maintain a `updated_at` column:

```sql
ALTER TABLE tickets
  ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
  ON UPDATE CURRENT_TIMESTAMP;
```

(MySQL has `ON UPDATE CURRENT_TIMESTAMP` natively, unlike Postgres or Snowflake.)

## Gotchas

1. **One DB per connector.** No schema layer in the URI; the `database`
   config field is the scope.
2. **`text_fields` lowercase by default** (same as postgres), unless the
   table uses quoted mixed-case column names.
3. **`grep` case-sensitivity follows the column collation.** Most CI/CD
   defaults (`utf8mb4_unicode_ci`, `utf8mb4_general_ci`) are case-insensitive.
4. **Password redacted on persistence.** Always use `credential_ref` — an
   inline `password = "..."` works on first run but won't survive a worker
   reopen (cat/grep would then fail to authenticate).
5. **MySQL `JSON` columns**: the connector returns them as Python dicts;
   address nested fields via JSONPath-lite (`payload.user.email`).
6. **Row-level filtering**: not configurable. Use a VIEW upstream
   (`CREATE VIEW open_tickets AS SELECT ... WHERE status = 'open'`) and map
   `[[objects]]` against the view.
