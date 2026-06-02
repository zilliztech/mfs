# mysql connector — ingest

URI: `mysql://<alias>` (alias is free-form).

## How to obtain credentials

Four fields: `host`, `port`, `database`, `user`, `password`. Compose by
hand or pull from your existing app config / `~/.my.cnf`.

**Probe** before MFS sees them:
```bash
mysql -h <host> -P <port> -u <user> -p<pw> <database> -e "SELECT 1"
mysql ... -e "SHOW TABLES"
```

## Required scopes

Read-only user:
```sql
CREATE USER 'mfs_reader'@'%' IDENTIFIED BY 'xxx';
GRANT SELECT ON prod.* TO 'mfs_reader'@'%';
FLUSH PRIVILEGES;
```

## Required toml fields

| key | what |
|---|---|
| `host` | server hostname / IP |
| `database` | database (schema) name |
| `user` | username |
| `password` | password (use `env:MYSQL_PASSWORD`) |

## Optional

| key | default | meaning |
|---|---|---|
| `port` | 3306 | server port |
| `cursor_column` | _none_ | incremental sync column, typically `updated_at` |
| `max_read_rows` | 100000 | per-table cap |

## `[[objects]]` blocks

Same shape as Postgres — each table needs `text_fields` declared:

```toml
[[objects]]
match = "tickets"
text_fields = ["title", "description"]
locator_fields = ["id"]
```

MySQL uses backtick identifiers internally; the wizard's safe_ident
helper handles this. Don't include backticks in `match`.

## env: example

```toml
host = "db.example.com"
port = 3306
database = "prod"
user = "mfs_reader"
password = "env:MYSQL_PROD_PASSWORD"
cursor_column = "updated_at"

[[objects]]
match = "tickets"
text_fields = ["title", "description"]
locator_fields = ["id"]
```

## Pitfalls

- **`utf8` vs `utf8mb4`**: legacy `utf8` doesn't carry 4-byte chars
  (most emoji, some CJK). If `mfs cat` shows mojibake, the column is on
  `utf8` not `utf8mb4`.
- **`SET GLOBAL net_read_timeout`**: long table scans on slow networks
  can hit the server's `net_read_timeout`. Either fix server config or
  lower `max_read_rows`.
- **No `cursor_column` configured**: every sync rescans the whole
  table. Pick `updated_at` if it exists.
