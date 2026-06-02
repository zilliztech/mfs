# postgres connector — search & browse

## URI tree

```
postgres://<alias>/
└── <schema>/
    └── <table>/
        ├── rows.jsonl              ← lazy NDJSON; each row is one record
        └── schema.json             ← one-shot table schema (columns + types)
```

`schema.json` is its own indexed chunk (`schema_summary` kind) — useful
for finding "which table has a column named X". `rows.jsonl` is the
lazy stream of per-row records.

## Record shape

One JSON per row, all DB columns at top level. Types map per asyncpg:
strings stay strings, timestamps become ISO-8601 strings, JSONB
columns appear as nested dicts.

```json
{"id": 12345, "title": "Auth bug in SSO flow", "description": "When users...",
 "status": "open", "assignee": "alice", "updated_at": "2026-06-01T10:30:00Z"}
```

## Locator

`mfs cat postgres://<alias>/<schema>/<table>/rows.jsonl --locator
'{"id": 12345}'` reopens that row.

For composite PKs the locator is the full key:
```json
{"org_id": 7, "user_id": 999}
```

## Indexed chunk kinds

- **`row_text`** (per row, from `[[objects]] text_fields`): the
  main searchable content. One chunk per row.
- **`schema_summary`** (per table): table name + column list + types.
  Helps `mfs search "where is the email column"` etc.

## Search strategy

| Query intent | Recommended |
|---|---|
| "find rows about X" | `mfs search "X" postgres://<alias>/<schema>/<table>/rows.jsonl` |
| "which table contains Y" | `mfs search "Y" postgres://<alias> --kind schema_summary` |
| Exact ID lookup | `mfs cat <table>/rows.jsonl --locator '{"id": N}'` — skip search |
| Regex over a column | `mfs grep` does NOT regex on postgres; use SQL pushdown via the connector's grep impl OR `mfs export` + local `grep` |
| Big table sampling | `mfs head <uri>/<schema>/<table>/rows.jsonl -n 20` |

## Field semantics caveats

- The chunk's `content` is whatever `text_fields` were configured at
  ingest time. If a search misses something you'd expect to find,
  `mfs head` to see if the field is even in the row, OR check the
  connector toml for `text_fields`.
- `metadata_fields` in the search hit's `metadata` dict are great for
  client-side filtering ("only show me hits with status=open").

## Large-dataset tips

- Tables with millions of rows: search is fine (Milvus is fast), but
  `mfs cat --range 0:100000` on `rows.jsonl` is a sequential scan.
  Use `--locator` for known IDs instead.
- Schema with hundreds of tables: `mfs tree -L 3 postgres://<alias>`
  to orient.
- A `partial` search_status often means `max_read_rows` capped a big
  table. The search still works but coverage isn't 100%.
