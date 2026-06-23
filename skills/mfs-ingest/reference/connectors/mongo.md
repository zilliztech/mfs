# mongo connector — ingest

URI: `mongo://<alias>`.

## How to obtain credentials

A MongoDB connection URI:
```
mongodb://user:pass@host:27017/?authSource=admin
mongodb+srv://user:pass@cluster.mongodb.net/?retryWrites=true
```

**Probe** with `mongosh`:
```bash
mongosh "$MONGO_URI" --eval "db.adminCommand({ping: 1})"
```

## Required scopes

A user with read access to the target database:
```js
use admin
db.createUser({
  user: "mfs_reader",
  pwd: "xxx",
  roles: [{ role: "read", db: "prod" }]
})
```

## Required toml fields

| key | what |
|---|---|
| `uri` | the MongoDB URI (`env:MONGO_URI` recommended) |
| `database` | the database name (NOT specified in the URI's path) |

## Optional

| key | default | meaning |
|---|---|---|
| `cursor_field` | _none_ | field used to strengthen the collection fingerprint, often `updatedAt` or `_id` (Mongo `_id` is ObjectId which encodes time) |
| `max_read_docs` | 100000 | per-collection cap |

## `[[objects]]` blocks

Mongo documents are heterogeneous — each collection needs explicit
`text_fields` declaration:

```toml
[[objects]]
match = "/support_threads"
text_fields = ["title", "messages[].body"]
locator_fields = ["_id"]
metadata_fields = ["status", "created_at"]
```

`text_fields` supports JSONPath-lite syntax:
- `field` → top-level scalar
- `nested.field` → dotted nested
- `array[].field` → flatten array of objects

## env: example

```toml
uri = "env:MONGO_URI"
database = "prod"
cursor_field = "updatedAt"

[[objects]]
match = "/tickets"
text_fields = ["title", "description"]
locator_fields = ["_id"]

[[objects]]
match = "/kb_articles"
text_fields = ["title", "body"]
locator_fields = ["_id"]
```

## Pitfalls

- **ObjectId locators**: `_id` values are 24-char hex when serialized.
  `mfs cat --locator '{"_id": "65a3..."}'` works.
- **Sharded clusters**: use `mongodb+srv://` to let the driver find
  shards. Specifying a single shard's host in the URI is brittle.
- **`cursor_field` is object-level today**: it helps MFS notice
  in-place document edits by including the collection's max cursor value
  in the fingerprint. When the fingerprint changes, the collection's
  `documents.jsonl` object is re-read and re-indexed; MFS does not patch
  individual documents yet.
