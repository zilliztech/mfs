# bigquery connector — search & browse

## URI tree

```
bigquery://<alias>/
└── <dataset>/
    └── tables/
        └── <table>/
            ├── rows.jsonl
            └── schema.json
```

Datasets are configured in the toml's `datasets` list; only those
appear under the URI.

## Record / locator / chunk kinds

Same as postgres. Record shape: BigQuery columns at top level; STRUCT
columns become nested objects, ARRAY columns become arrays.

## Search strategy

| Intent | Use |
|---|---|
| Find rows | `mfs search "X" bigquery://<alias>/<dataset>/tables/<table>/rows.jsonl` |
| Schema discovery | `mfs search "Y" bigquery://<alias> --kind schema_summary` |
| ID lookup | `mfs cat --locator '{"<pk>": <value>}'` |

## Pitfalls

- **STRUCT / ARRAY columns**: `text_fields` configured at ingest can
  reach into them (e.g. `event_properties.user_id` or `tags[]`). If
  `mfs cat --range 0:3` shows the values but search misses, check the
  ingest config.
- **No incremental sync (yet)**: every `mfs add` full-scans the
  configured tables. For very large tables this is expensive.
- **Cost on the BigQuery side**: `list_rows` is free for the first
  10TB scanned per month, then $5/TB. The connector uses tabledata.list
  (not SQL queries), so no compute slot cost.
