# MongoDB (`mongo`)

The `mongo` connector indexes documents from a single MongoDB database. Each
document becomes a searchable record, and each collection gets a sampled schema
preview (Mongo has no fixed schema, so it's inferred from a sample).

## How MFS sees it

Collections sit under the alias; each exposes its documents and a sampled schema:

```text
mongo://prod-cluster/
└── support_threads/
    ├── documents.jsonl   record_collection  → one searchable chunk per document
    └── schema.json       table_schema       → sampled field summary
```

Documents are chunked per-document and need `text_fields` to become searchable.

## Credentials

A MongoDB connection URI, in either form:

```text
mongodb://user:pass@host:27017/?authSource=admin
mongodb+srv://user:pass@cluster.mongodb.net/?retryWrites=true
```

For Atlas, copy the SRV URI from *Database → Connect → Drivers* and substitute
the real password. Before copying it, open *Database Access → Add New Database
User*, create a user with the built-in **read** role on the target database, then
open *Network Access* and allow the egress IP of the machine or container running
`mfs-server`. A read-only user is enough — the connector only runs `find()`.

Probe from the server host before MFS sees it:

```bash
mongosh "$MONGO_URI" --eval "db.adminCommand({ping: 1})"
```

If the password contains `@`, `:`, `/`, or other URL characters, percent-encode
it before putting it in the URI.

## Configuration

```toml
uri = "env:MONGO_URI"
database = "prod"
cursor_field = "updatedAt"     # or _id; enables incremental re-sync
max_read_docs = 100000

[[objects]]
match = "/support_threads"
text_fields = ["title", "messages[].body"]
locator_fields = ["_id"]
metadata_fields = ["status"]
```

`text_fields` supports nested paths like `messages[].body` to pull text out of
arrays of subdocuments.

Keep the URI in the server environment, then probe and index:

```bash
export MONGO_URI='mongodb+srv://mfs_reader:<password>@cluster.mongodb.net/'
mfs connector probe mongo://prod-cluster --config ./mongo.toml
mfs add mongo://prod-cluster --config ./mongo.toml
```

## Sync and freshness

With `cursor_field` set (`updatedAt` or `_id`), re-syncs pull only documents
changed since the last run; deletions are caught by `full_scan`.

## Search and browse

```bash
mfs search "refund escalation" mongo://prod-cluster/support_threads/documents.jsonl
mfs cat mongo://prod-cluster/support_threads/documents.jsonl --locator '{"_id":"65a3..."}'
```

## Pitfalls

- Documents are heterogeneous; fields absent from a given document are simply
  skipped when rendering its text.
- `_id` locators use the serialized string form, not `ObjectId(...)`.
- `max_read_docs` caps large collections and can mark recall partial.
