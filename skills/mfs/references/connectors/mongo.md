# mongo connector (`mongo://`)

## What this is

MongoDB document store (Atlas / self-hosted / any wire-compatible). The
connector talks to one database via `pymongo`'s native async client
(`AsyncMongoClient`, available since pymongo 4.13). Each collection becomes a
directory; documents are streamed lazily.

**When MFS helps**: large operational stores (events, profiles, logs as JSON
docs) where you want semantic search across nested fields without writing
aggregation pipelines.

**Cost note**: MongoDB hits only on sync + `cat --locator` / `head`.
`search` is local Milvus.

## URI shape

```
/                                          (root: collections of the configured DB)
/users/                                    collection folder
/users/documents.jsonl                     lazy document stream
/users/schema.json                         sampled-from-first-doc schema
```

object_kind = `record_collection` (not `table_rows`) because Mongo docs are
inherently nested + polymorphic — the JSONPath-lite paths (`a.b`, `a[*].b`)
in `text_fields` are particularly useful here.

## Auth

Standard MongoDB connection URI carries the credentials:

```toml
credential_ref = "env:MONGO_URI"
# resolves to e.g.
# "mongodb+srv://mfs:hunter2@cluster0.xxx.mongodb.net/prod?retryWrites=true&w=majority"
```

The URI specifies user/pass/host/replica-set/SSL/etc. Don't put `uri` as plain
text in the config — it gets redacted on persistence.

## Connector config TOML

```toml
# ─── connection (required) ───
database       = "prod"               # which DB inside the cluster
credential_ref = "env:MONGO_URI"

# ─── optional ───
# max_read_docs = 200000           # LIMIT per collection scan; default 100000
# cursor_field  = "updatedAt"      # ISODate or timestamp field that monotonically rises

# ─── per-collection field mapping ───
[[objects]]
match           = "/users/documents.jsonl"
text_fields     = ["name", "bio", "tags[*]"]   # JSONPath-lite shines on docs
locator_fields  = ["_id"]                       # default if omitted
metadata_fields = ["country", "createdAt"]
# indexable = true
# chunk_max = 1_000_000
```

### JSONPath-lite syntax (especially useful on Mongo)

| Path | Meaning |
|---|---|
| `a` | top-level field |
| `a.b` | nested dict |
| `a[*].b` or `a[].b` | every element of an array → flattened list |
| `a[2].b` | indexed element |
| `a[0:5].b` | slice |

`_id` is auto-stringified (ObjectId → hex) so the locator round-trips
cleanly through `mfs cat --locator '{"_id":"6650..."}'`.

## What each command does

| Command | Behaviour |
|---|---|
| `mfs ls /` | `list_collection_names()` on the configured database. |
| `mfs ls /<coll>/` | `["documents.jsonl", "schema.json"]`. |
| `mfs cat /<coll>/documents.jsonl` | **refused** (lazy). |
| `mfs cat .../documents.jsonl --range A:B` | `find().skip(A).limit(B-A)`. |
| `mfs cat .../documents.jsonl --locator '{"_id":"..."}'` | `find_one({"_id": ObjectId(...)})`. The connector auto-converts the string back to ObjectId. |
| `mfs head -n N .../documents.jsonl` | `find().limit(N)`. `head_cache` artifact on repeat runs. |
| `mfs cat .../schema.json` | shape sampled from the first document (best-effort, since Mongo schemas are polymorphic). |
| `mfs grep PATTERN .../documents.jsonl` | scans documents linearly (no pushdown). |
| `mfs search "QUERY"` | Milvus only. `row_text` chunks per document. |

## Typical workflow

```bash
# 1. Mongo-side: create a narrow read-only user.
#   db.createUser({user:"mfs_ro", pwd:"...", roles:[{role:"read", db:"prod"}]})

# 2. Server env.
export MONGO_URI="mongodb+srv://mfs_ro:...@cluster0.xxx.mongodb.net/prod"

# 3. Register.
mfs add mongo://prod --config mongo-prod.toml

# 4. Use.
mfs search "active user korea" --connector-uri mongo://prod
mfs cat mongo://prod/users/documents.jsonl --locator '{"_id":"66501a..."}'

# 5. Refresh incrementally.
mfs add mongo://prod --no-full
```

## Incremental sync

Per-collection fingerprint = `count_documents({}) | max(cursor_field)`. If you
don't configure `cursor_field` and there's no obvious monotonic field on the
docs, only INSERT / DELETE are detected — in-place document updates are
missed. Add `updatedAt: { $set: new Date() }` in your write paths to make
updates detectable.

## Gotchas

1. **One DB per connector** — same as MySQL. Multiple Mongo DBs = multiple
   `mfs add` calls.
2. **`_id` is an ObjectId**, not a string. The connector stringifies it on
   read; on `cat --locator` you pass the string and the connector parses it
   back to ObjectId. Other types (UUID, integer-keyed `_id`) work too —
   pass them in their stringified JSON form (`{"_id": 42}`,
   `{"_id":"alice@example.com"}`).
3. **Schemas are polymorphic** — `schema.json` is a sample from doc #1.
   Fields appearing in later documents won't be in `schema.json`. For LLM
   summary purposes this is fine; for `text_fields` choose the field names
   you know are stable.
4. **Array fields**: use `tags[*]` to flatten into the embedding text.
   `tags` alone embeds the Python list `repr`, which is rarely what you want.
5. **`grep` is slow** — no native pushdown to Mongo for arbitrary patterns.
   Use `search` (which is vector / BM25 over the indexed chunks).
6. **Atlas free tier connection limit**: keep `max_read_docs` modest; one
   long-running cursor counts as a connection.
