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
columns become nested objects, ARRAY columns become arrays. `schema.json`
is always browsable; `schema_summary` is searchable only when
`[summary].enabled` is on.

## Search strategy

| Intent | Use |
|---|---|
| Find rows | `mfs search "X" bigquery://<alias>/<dataset>/tables/<table>/rows.jsonl` |
| Schema discovery | `mfs search "Y" bigquery://<alias> --kind schema_summary` if summaries are enabled; otherwise `mfs cat bigquery://<alias>/<dataset>/tables/<table>/schema.json` |
| ID lookup | `mfs cat --locator '{"<pk>": <value>}'` |

## Pitfalls

- **STRUCT / ARRAY columns**: `text_fields` configured at ingest can
  reach into them (e.g. `event_properties.user_id` or `tags[]`). If
  `mfs cat --range 1:4` shows the values but search misses, check the
  ingest config.
- **No row-level incremental sync yet**: the connector uses table
  metadata (`num_rows` + `modified`) as the object fingerprint. When it
  changes, the table's `rows.jsonl` object is re-read and re-indexed.
- **BigQuery-side limits**: the connector uses `tabledata.list` through
  `list_rows` (not SQL query jobs), so watch API quota / throughput
  limits rather than query slot cost.
