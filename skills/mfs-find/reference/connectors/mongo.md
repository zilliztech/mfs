# mongo connector — search & browse

## URI tree

```
mongo://<alias>/
└── <collection>/
    ├── documents.jsonl             ← one document per line
    └── schema.json                 ← sampled field preview
```

Mongo collections are schemaless, so `schema.json` is inferred from a sample
document and should be treated as a preview.

## Record shape

Each document is the BSON converted to JSON. ObjectId becomes a
24-char hex string. Nested subdocs and arrays stay nested.

```json
{"_id": "65a3...", "title": "RFC", "body": "...", "tags": ["arch", "v2"],
 "comments": [{"author": "alice", "body": "..."}, ...]}
```

## Locator

```bash
mfs cat mongo://<alias>/<collection>/documents.jsonl --locator '{"_id": "65a3..."}'
```

For custom PKs configured at ingest time (e.g. `locator_fields = ["uuid"]`),
use that field instead.

## Chunk kind

`row_text` per document. Content shape depends on `text_fields` in the
ingest toml; typically `["title", "body", "comments[].body"]` — the
last form flattens arrays.

## Search strategy

| Intent | Use |
|---|---|
| "find documents about X" | `mfs search "X" mongo://<alias>/<collection>/documents.jsonl` |
| Doc by `_id` | `mfs cat mongo://<alias>/<collection>/documents.jsonl --locator '{"_id": "..."}'` |
| Nested field search | depends on `text_fields` — if `comments[].body` was indexed, search hits the array values; if not, only top-level fields |

## Pitfalls

- **Heterogeneous docs**: collections without a schema can have docs
  with completely different shapes. A `text_fields = ["body"]` ingest
  silently skips docs that don't have a `body` field. `mfs head` to
  sanity-check.
- **`_id` rendering**: ObjectIds in `--locator` JSON must be the
  string form, not the BSON ObjectId() constructor.
- **TTL collections**: documents disappear over time without warning.
  Re-sync so the collection object is re-read and stale chunks are
  replaced.
