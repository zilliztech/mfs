# Schema design

MFS persists state across a few stores: a **metadata database** for bookkeeping
(it also holds the work queue), a separate **transformation-cache database** for
memoized model outputs, the **Milvus collection** for searchable vectors, and an
**object store** (the local filesystem by default) for artifact bytes. The two
relational databases are SQLite locally and Postgres at scale. This page walks
each one. The vocabulary — connector, object, ConnectorJob, ObjectTask, chunk — is
defined in [Architecture](architecture.md#core-concepts).

## The metadata database

One relational database holds everything MFS knows about your sources and the
work it's doing on them. The tables:

| Table | One row per | Holds |
|---|---|---|
| `connectors` | registered source | root URI, type, status, the config JSON and its hash, the `credential_ref`. Unique on `(namespace_id, root_uri)`. |
| `objects` | object under a connector | `object_uri`, `parent_path`, media type, `fingerprint` (its change token), `indexable`, `search_status`, `chunk_count`. Keyed by `(connector_id, object_uri)`. |
| `connector_jobs` | ConnectorJob (one sync) | status, heartbeat, the object counts (total / succeeded / failed / cancelled), error. |
| `object_tasks` | ObjectTask (one object's work) | `change_kind`, status, `priority`, `attempts`, `last_error`. This **is** the durable queue. |
| `connector_state` | per-connector key/value | sync cursors and other small connector-owned state. Keyed by `(connector_id, key)`. |
| `file_state` | a file in an uploaded tree | `size`, `mtime_ns`, `inode`, `sha1`, `status`, `renamed_from` — the upload manifest that makes re-syncs send only what changed. |
| `artifact_cache` | a derived blob | a pointer to the bytes (`storage_path`) plus a `source_key`; the bytes themselves live on disk. See [Caching](caching.md). |
| `watch_grants` | a granted watch path | bookkeeping for watch scopes. |
| `schema_version` | — | the single integer that lets the server fail fast if a database predates the current schema. |

Two design points worth calling out:

- **The queue is a table.** `object_tasks` *is* the work queue — workers claim
  rows by `(priority, status)`, so there's no Redis or Celery to run. And
  `connector_jobs` carries two partial-unique indexes (at most one `running` and
  one `preparing`/`queued` job per connector), which is what makes a double
  `mfs add` safe at the database level.
- **It's only *knowledge*, never the truth.** Every row here describes an
  upstream source or derived work; none of it is the source data itself. Drop the
  whole database and a re-sync rebuilds it.

## The transformation-cache database

Model outputs — embeddings, image (VLM) descriptions, summaries — are memoized in
their **own** database, separate from the metadata DB (a standalone SQLite file by
default, or a shared Postgres). Keeping it apart is deliberate: it's high-churn,
best-effort data whose loss only costs recompute, so it never sits on the metadata
DB's transactional path. It has one table:

| Table | One row per | Holds |
|---|---|---|
| `transformation_cache` | one memoized model call | `cache_key` (primary key), `kind` (`embedding` / `vlm` / `summary`), `input_hash`, `provider`, `model`, `model_version`, `output_bytes`, `hit_count`, `last_hit_at`. |

The `cache_key` folds the input hash together with the provider, model, and
version, so a hit is only ever returned for the exact same input *and* model. See
[Caching](caching.md) for how it's used.

## The Milvus collection

All searchable chunks live in Milvus. By default they share **one collection**,
whose name bakes in the schema version and the embedding dimension
(`mfs_chunks__v{N}_d{dim}`), so changing the schema or swapping the embedding
model targets a *fresh* collection instead of corrupting the current one.

| Field | Type | What it is |
|---|---|---|
| `chunk_id` | VARCHAR (primary key) | The chunk's content address — `sha1(namespace + connector + object + chunk_kind + locator)`. Makes every write an idempotent upsert. |
| `connector_uri` | VARCHAR (**partition key**) | Which connector the chunk belongs to. |
| `object_uri` | VARCHAR | Which object it came from — the URI you feed to `cat`. |
| `locator` | JSON | Where inside the object — `{"lines":[42,78]}`, `{"id":123}`, a thread key. |
| `content` | VARCHAR | The text that was embedded and is BM25-analyzed for keyword search. |
| `dense_vec` | FLOAT_VECTOR | The embedding (semantic search). |
| `sparse_vec` | SPARSE_FLOAT_VECTOR | The BM25 vector — generated *by Milvus* from `content`, so writers never supply it. |
| `chunk_kind` | VARCHAR | `body`, `row_text`, `thread_aggregate`, `directory_summary`, … |
| `metadata` | JSON | The connector's `metadata_fields` (status, author, …) for filtering and display. |
| `namespace_id` | VARCHAR | The namespace, for multi-tenant isolation. |
| `indexed_at` | INT64 | When the chunk was written. |

Indexes: `dense_vec` uses AUTOINDEX with cosine distance, `sparse_vec` a
sparse-inverted BM25 index, and `namespace_id` / `object_uri` / `chunk_kind` get
scalar inverted indexes for fast filtering (skipped on Milvus Lite, which falls
back to a scan).

### Connectors, namespaces, and the partition key

Three things scope a chunk, at three levels:

- **Connector** is the **partition key** (`connector_uri`). Each connector's
  chunks sit in their own partition, so a scoped query
  (`mfs search "…" postgres://prod`) only touches that connector's partition,
  while `--all` fans across them.
- **Namespace** is the tenant boundary. By default (`collection_strategy =
  "shared"`) all namespaces share the one collection and are separated by the
  `namespace_id` field; set `collection_strategy = "per_namespace"` and each
  namespace gets its own collection (`mfs_chunks__{namespace}__v{N}_d{dim}`) for
  hard isolation. Today MFS runs a single `default` namespace with zero config;
  per-user / per-workspace namespaces are a [v0.5+ direction](roadmap.md).
- **Schema and model version** are baked into the collection name, so an
  incompatible change — a new embedding dimension, a schema bump — lands in a
  fresh collection instead of mixing with the old one.

### How the fields map to your data

Beyond those, the fields are just your data, addressed at three levels of
granularity:

```text
connector_uri   postgres://prod          ← which source
   object_uri   …/public/tickets/rows.jsonl   ← which object (file / row set / thread)
      locator   {"id": 12345}            ← which slice inside it
      content   "Login broken after SSO migration …"   ← the text that got embedded
   chunk_kind   row_text                 ← what kind of chunk it is
     metadata   {"status":"open","priority":"high"}   ← connector side-fields
```

A search hit hands back `object_uri` + `locator`, which is exactly what `cat
--locator` needs to reopen the exact row, line range, or thread — the index
points at your source, it doesn't replace it.
