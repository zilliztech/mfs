# Caching

The two expensive parts of ingest are **converting** a source — turning a PDF or
Office doc into text — and **calling models** — embeddings, image (VLM)
descriptions, and summaries. MFS keeps two caches so it almost never pays for the
same work twice. They sit at different points and are keyed differently, so it's
worth knowing which holds what.

| | Artifact cache | Transformation cache |
|---|---|---|
| Holds | converted object bytes — Markdown from a PDF, a structured head preview | model-call outputs — embeddings, image (VLM) descriptions, summaries |
| Keyed by | object + kind | content hash + model |
| Reused | per object, to serve reads | across objects, connectors, and namespaces |
| Bytes live in | the filesystem / object store (a row in `artifact_cache` points to them) | its own table (`transformation_cache`) |
| If you lose it | re-converted on next read | recomputed on next sync |

Neither is a source of truth — both are derived, and losing either costs
recompute, not correctness.

## Artifact cache

When MFS converts an object (a PDF or Office doc → Markdown) or grabs a head
preview of a structured object, it stores the result as an **artifact** so the
next `cat` / `head` / `tail` reads the converted bytes instead of going back to
the connector and converting again. The kinds in use today are `converted_md`
(converted document text) and `head_cache` (a structured-object preview). An
image's VLM description is *not* an artifact — it's a model output, so it lives in
the transformation cache below.

The bytes live on the filesystem (or object store); the
[`artifact_cache` table](schema.md#the-metadata-database) is just the index —
keyed by `(namespace_id, object_uri, artifact_kind)`, with a `storage_path` and a
`source_key`. That `source_key` is a freshness token combining the source content
with the converter's identity: on the next sync, if it still matches, the artifact
is reused as-is; if the file changed or the converter was upgraded, it's rebuilt.

So the artifact cache is **per object**: it's what makes browsing a big PDF or a
converted doc fast, and it's the shared input the [Job Lane](ingest-pipeline.md)
folds when it summarizes a directory — the children are read from here, not
re-converted.

## Transformation cache

Embeddings, image (VLM) descriptions, and summaries are the calls that cost real
money and latency. The transformation cache memoizes each one, keyed by **content
and model** rather than by object, so identical input never gets sent to a
provider twice. (This is where an image's description lives — it's a model output,
not a converted artifact.)

The key is `cache_key = sha1(input_hash + kind + provider + model + version +
config)` — where `input_hash` is the hash of the raw input text or bytes. Because
the model identity is *in* the key, a hit is valid no matter where the same
content shows up: a different object, a different connector, even after the Milvus
collection is rebuilt or the embedding model is rolled back to a prior version.
Each row also records `last_hit_at`, so the cache can be evicted LRU.

Two more properties matter:

- **Single-flight.** If several tasks miss on the same key at once, a lock lets
  exactly one of them make the provider call; the rest wait and reuse the result,
  instead of all firing the same expensive request.
- **Cross-lane.** The Object Lane and the Job Lane share this cache, so the Job
  Lane summarizing an image draws on the same VLM result the Object Lane already
  computed — never a second call for the same input.

This is what makes the everyday cases in [Robustness](robustness.md) cheap:
switching git branches re-embeds only the files whose content actually changed,
and rebuilding the index from scratch still hits the cache for every chunk whose
content and model are unchanged.

For the bandwidth side of the story — uploading only changed bytes — see the
`file_state` manifest in [Schema design](schema.md#the-metadata-database).
