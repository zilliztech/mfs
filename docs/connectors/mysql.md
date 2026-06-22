# MySQL (`mysql`)

The `mysql` connector indexes table rows from a single MySQL database as
searchable records, with a schema summary per table. One connector covers one
database; register another connector for another database.

## How MFS sees it

The configured database is the connector scope, so tables sit directly under the
alias:

```text
mysql://prod-db/
├── tickets/
│   ├── rows.jsonl     table_rows    → one searchable chunk per row
│   └── schema.json    table_schema  → searchable column summary
└── orders/
    ├── rows.jsonl
    └── schema.json
```

Rows are chunked per-row and need `text_fields` to become searchable (see
`[[objects]]`).

## Credentials

Five fields: `host`, `port`, `database`, `user`, `password`. Pull them from your
app config, or create a dedicated read-only user:

```sql
CREATE USER 'mfs_reader'@'%' IDENTIFIED BY '<password>';
GRANT SELECT ON prod.* TO 'mfs_reader'@'%';
```

Use the server's egress host instead of `%` when you can. For managed MySQL,
also allow the server's IP or security group before testing. Confirm
connectivity from the machine that runs the server before handing credentials to
MFS:

```bash
mysql -h <host> -P <port> -u <user> -p<pw> <database> -e "SHOW TABLES"
```

## Configuration

```toml
host = "db.example.com"
port = 3306
database = "prod"
user = "mfs_reader"
password = "env:MYSQL_PASSWORD"
cursor_column = "updated_at"   # enables incremental re-sync
max_read_rows = 100000

[[objects]]
match = "/tickets"
text_fields = ["title", "description"]
locator_fields = ["id"]
```

Keep the password in the server environment, then probe and index:

```bash
export MYSQL_PASSWORD='<password>'
mfs connector probe mysql://prod-db --config ./mysql.toml
mfs add mysql://prod-db --config ./mysql.toml
```

## Sync and freshness

With `cursor_column` set (usually `updated_at`), re-syncs pull only rows changed
since the last run; deletions are caught by `full_scan`. `grep` is a pushdown
straight to MySQL.

## Search and browse

```bash
mfs search "billing bug" mysql://prod-db/tickets/rows.jsonl
mfs search "email column" mysql://prod-db --kind schema_summary
mfs cat mysql://prod-db/tickets/rows.jsonl --locator '{"id":12345}'
```

## Pitfalls

- One connector = one database.
- No `text_fields` → browsable rows, but no row search.
- If probe fails while a local `mysql` command works, test from the server host
  or container; connector credentials are resolved there.
- Legacy `utf8` (3-byte) collations can return mojibake for 4-byte characters;
  prefer `utf8mb4`.
- Long scans can hit server timeouts; lower `max_read_rows` while testing.
