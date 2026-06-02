# mysql connector — search & browse

## URI tree

```
mysql://<alias>/
└── <table>/
    ├── rows.jsonl
    └── schema.json
```

Note: MySQL doesn't have schemas-within-databases like Postgres. The
database name is fixed in the connector toml; tables live one level
deep.

## Record / locator / chunk kinds

Same shape as postgres (see `postgres.md`). One row → one `row_text`
chunk; per-table `schema.json` → one `schema_summary` chunk.

## Search strategy

| Intent | Use |
|---|---|
| "find rows about X" | `mfs search "X" mysql://<alias>/<table>/rows.jsonl` |
| "which table has Y column" | `mfs search "Y" mysql://<alias> --kind schema_summary` |
| ID lookup | `mfs cat <table>/rows.jsonl --locator '{"id": N}'` |

## Pitfalls

- **`utf8` vs `utf8mb4`**: legacy `utf8` mangles 4-byte chars
  (emoji, some CJK). If `mfs cat` returns mojibake, that's the source
  table's collation, not MFS.
- **No schema layer**: a single connector covers one database. To
  search across multiple databases register one connector per database.
