# snowflake connector — search & browse

## URI tree

```
snowflake://<alias>/
└── <DATABASE>/
    └── <SCHEMA>/
        └── tables/
            └── <TABLE>/
                ├── rows.jsonl
                └── schema.json
```

Snowflake folds unquoted identifiers to UPPERCASE, so paths come back
uppercase. `mfs tree snowflake://<alias> -L 4` to see what's actually
there.

## Record / locator / chunk kinds

Same as postgres — `row_text` per row, `schema_summary` per table.
Column names in `metadata` will be UPPERCASE.

## Search strategy

| Intent | Use |
|---|---|
| Find row content | `mfs search "X" snowflake://<alias>/<DB>/<SCHEMA>/tables/<TABLE>/rows.jsonl` |
| Schema discovery | `mfs search "Y" snowflake://<alias> --kind schema_summary` |
| Per-row lookup | `mfs cat --locator '{"ID": 12345}'` (column names UPPERCASE) |

## Pitfalls

- **UPPERCASE everything**: `--locator '{"id": 1}'` won't match if the
  PK column is stored as `ID`. Match the case.
- **Warehouse auto-suspend**: first search on a cold connector waits
  for the warehouse to resume (~30s).
- **Big tables**: `max_read_rows` cap likely truncates large tables.
  Watch for `partial` in `mfs status`.
