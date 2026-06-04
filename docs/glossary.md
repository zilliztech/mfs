# Glossary

Compact lookup for MFS v0.4 terms and identifiers. Use this page to decode
CLI, API, configuration, search, and connector examples, then follow the linked
source page for the full workflow.

## Source Of Truth

| Need | Start here | Also verify in |
|---|---|---|
| First local run and success checkpoints | [Quickstart](getting-started.md) | [Troubleshooting](troubleshooting.md) |
| Exact command shape and flags | [CLI Reference](cli.md) | `cli/src/main.rs` |
| Search result fields and reopen patterns | [Search and Browse](search-and-browse.md) | [HTTP API](api.md), `server/python/src/mfs_server/api/models.py` |
| Connector targets and TOML | [Connectors](connectors.md) | [Connector Reference](connector-reference.md), `server/python/src/mfs_server/connectors/registry.py` |
| Server/CLI config, env vars, and local files | [Configuration](configuration.md) | [Architecture](architecture.md), `server/python/src/mfs_server/config.py` |
| API endpoints, schemas, and errors | [HTTP API](api.md) | `protocol/openapi.yaml`, `protocol/errors.md` |

## Identifiers

| Term | Meaning | Common shape | Go next | Verified in |
|---|---|---|---|---|
| Source URI | User-facing URI for a searchable or browsable source/object. In search JSON, use the returned `source` instead of reconstructing it. | `file://local/tmp/mfs-quickstart/README.md` | [Search and Browse](search-and-browse.md) | `ResultEnvelope.source` |
| Connector | Server-side plugin plus registered state for one source tree. Connectors expose sources as URI trees. | `file`, `web`, `postgres`, `slack` | [Connectors](connectors.md) | `connectors/registry.py` |
| Connector URI / root URI | Registered source root. `/v1/status` exposes it as `connectors[].root_uri`. Scope `search`, `ls`, `tree`, and `cat` to this root or a child; remove the connector by its registered root. | `postgres://prod-db`, `web://docs`, `file://local/abs/root` | [Connectors](connectors.md) | `ConnectorRow.root_uri` |
| Object URI / `source` field | Full object identity returned from search/grep/list/read surfaces. Feed it back to `cat`, `head`, `tail`, or `export`. | `postgres://prod/public/tickets/rows.jsonl` | [Search and Browse](search-and-browse.md) | `ResultEnvelope.source`, `LsEntry.path` |
| Locator | Per-hit identity inside an object. Text/code/document hits use `{"lines":[start,end]}`; structured hits use connector key fields. Copy it back to `cat --locator`. | `{"lines":[42,78]}`, `{"id":12345}` | [Search and Browse](search-and-browse.md) | `ResultEnvelope.locator` |
| Local canonical file URI | Same-host local paths resolve to a file connector root with the `local` authority. `file:///abs/path` is treated as a local path target. | `file://local/tmp/repo` | [CLI Reference](cli.md) | `Engine._resolve_target` |
| Upload connector URI | Upload mode gives a local client tree a stable logical file URI based on `client_id` and the client absolute root. | `file://<client_id>/abs/root` | [Deployment](deployment.md) | `ManifestResponse.connector_uri` |
| Endpoint | Server base URL selected by the CLI before it calls `/v1` paths. | `http://127.0.0.1:13619` | [Configuration](configuration.md) | `base_url()` |
| Namespace | Active server namespace. It defaults to `default`, appears in `/v1/server/info`, and scopes metadata/cache/Milvus records. | `default` | [Architecture](architecture.md) | `ServerInfo.namespace` |

!!! note "Internal object URI detail"
    Public docs use `source` or `path` for the full object identifier. Internally,
    the metadata DB stores `objects.object_uri` as connector-relative, while
    Milvus search rows store the full object URI.

## Search And Browse Fields

```json
{
  "source": "file://local/path/to/repo/README.md",
  "content": "Release checklist...",
  "score": 0.82,
  "locator": {"lines": [10, 18]},
  "metadata": {
    "kind": "search",
    "chunk_kind": "body",
    "fields": {}
  }
}
```

| Term | Meaning | Values or shape | Go next | Verified in |
|---|---|---|---|---|
| Result envelope | One search hit. The stable outer fields are `source`, `content`, `score`, `locator`, and `metadata`. | `SearchResponse.results[]` | [HTTP API](api.md) | `ResultEnvelope` |
| `metadata.fields` | Connector-provided side fields copied from configured or preset `metadata_fields`. Use for quick inspection, not as proof without reopening the object. | Object/map | [Connectors](connectors.md) | `metadata.fields` |
| Chunk kind | Index chunk category used by search filtering and result metadata. Current examples include `body`, `row_text`, `thread_aggregate`, `directory_summary`, `schema_summary`, and `vlm_description`. | `metadata.chunk_kind`, `--kind body,row_text` | [Search and Browse](search-and-browse.md) | `chunk_kind` |
| `search_status` | Per-entry index state returned by `mfs ls PATH --json`. `null` means the entry was listed but no object metadata row exists. | `indexed`, `partial`, `not_indexed`, `null` | [Troubleshooting](troubleshooting.md) | `LsEntry.search_status` |
| Search availability | Source-level search readiness values documented with errors; do not confuse them with `ls` entry values. | `available`, `partial`, `building`, `unavailable` | [Troubleshooting](troubleshooting.md) | `protocol/errors.md` |
| `via` | Grep match route. It helps explain whether a match came from connector pushdown, BM25, linear scan, or a notice. | `pushdown`, `bm25`, `linear`, `notice` | [Search and Browse](search-and-browse.md) | `GrepMatchModel.via` |
| `--all` | Search the whole namespace instead of one scoped path. Use when you do not know which registered source contains the answer. | `mfs search "quota" --all` | [CLI Reference](cli.md) | `Cmd::Search.all` |
| `--json` | Global CLI flag for raw JSON output. Use it when you need `source`, `locator`, `metadata`, `via`, `search_status`, or raw API fields. | `mfs --json search "query" PATH` | [CLI Reference](cli.md) | `Cli.json` |

## Ingest, Jobs, And Upload

| Term | Meaning | Values or shape | Go next | Verified in |
|---|---|---|---|---|
| Target | Path or connector URI sent to add/probe/estimate. | `AddRequest.target` | [HTTP API](api.md) | `AddRequest.target` |
| Job | Stored ingest/sync operation for a connector. Add, upload, and connector update return a job id. | `JobResponse` | [CLI Reference](cli.md) | `connector_jobs` |
| Job id | Identifier returned as `job_id`; poll with `mfs job show JOB_ID` or `GET /v1/jobs/{job_id}`. | Hex string | [HTTP API](api.md) | `AddResponse.job_id` |
| Job status | Current status is modeled as an open string. Current stored values include `preparing`, `queued`, `running`, `succeeded`, `failed`, and `cancelled`. | `JobResponse.status` | [Troubleshooting](troubleshooting.md) | `connector_jobs.status` |
| Upload mode | Client-side path transfer for servers that cannot read the client path directly. The manifest step sends stats; upload sends changed bytes. | `--upload`, `--force-upload`, `--no-upload` | [Deployment](deployment.md) | `/v1/files/manifest`, `/v1/files/upload` |
| `--force-index` / `--full` | Force a full re-index by ignoring caches/fingerprints. `--full` is the visible alias. | `mfs add --force-index PATH` | [CLI Reference](cli.md) | `Cmd::Add.force_index` |
| `--force-upload` | Re-send every file and force a full re-index in upload mode. | `mfs add --force-upload PATH` | [Troubleshooting](troubleshooting.md) | `upload_path(... resend_all)` |
| `--no-upload` | Force shared-filesystem mode: the server reads the path directly. | `mfs add --no-upload PATH` | [Troubleshooting](troubleshooting.md) | `Cmd::Add.no_upload` |
| Connector estimate | Zero-billing pre-flight for external targets when `mfs add` would otherwise prompt. It estimates objects, chunks, and tokens without embedding API calls. | `/v1/connectors/estimate` | [Connectors](connectors.md) | `EstimateResponse` |

## Client And Server Configuration

| Term | Meaning | Default or precedence | Go next | Verified in |
|---|---|---|---|---|
| `MFS_HOME` | Root for local CLI/server files. The server creates it when resolving defaults. | `~/.mfs`; Docker/Compose docs use `/data` | [Configuration](configuration.md) | `mfs_home()` |
| Profile | Local CLI endpoint record in `$MFS_HOME/client.toml`. Profiles have a name, URL, optional token, and one active profile. | `mfs profile add prod URL --token env:MFS_API_TOKEN` | [CLI Reference](cli.md) | `ClientConfig.profiles` |
| Endpoint precedence | How the CLI chooses the server base URL. | `MFS_API_URL` -> active profile URL -> `http://127.0.0.1:13619` | [Configuration](configuration.md) | `base_url()` |
| Bearer token | `Authorization: Bearer <token>` for `/v1` when server auth is enabled. CLI fallback is client behavior, not an HTTP API requirement. | `MFS_API_TOKEN` -> profile token -> `$MFS_HOME/server.token` | [HTTP API](api.md) | `_auth`, `auth_token()` |
| Server config lookup | How `mfs-server` loads `server.toml`. | `--config` -> `MFS_SERVER_CONFIG` -> `./server.toml` -> `$MFS_HOME/server.toml` -> `~/.mfs/server.toml` -> `/etc/mfs/server.toml` -> defaults | [Configuration](configuration.md) | `load_server_config()` |
| Connector TOML | File loaded by CLI `--config` and sent as the API `config` object. Structured sources commonly use `[[objects]]`. | `text_fields`, `locator_fields`, `metadata_fields` | [Connectors](connectors.md) | `AddRequest.config` |

## Storage And Indexing

| Term | Meaning | Local default | Go next | Verified in |
|---|---|---|---|---|
| Metadata database | Relational state for connectors, objects, jobs, tasks, connector state, and file upload state. SQLite is the local default; Postgres is configurable. | `$MFS_HOME/metadata.db` | [Configuration](configuration.md) | `storage/metadata/base.py` |
| Artifact cache | Per-object derived blobs used by read paths, such as converted document markdown and image descriptions. | `$MFS_HOME/cache` | [Architecture](architecture.md) | `storage/artifact_cache.py` |
| Transformation cache | Content-addressable memoization for convert, embedding, VLM, and summary results. Losing it costs recompute, not correctness. | `$MFS_HOME/transformation_cache.db` | [Configuration](configuration.md) | `storage/transformation_cache/` |
| Milvus / Milvus Lite | Chunk index for dense vectors and BM25 sparse vectors. Local default is Milvus Lite; remote Milvus or Zilliz is configured by URI/token. | `$MFS_HOME/milvus.db` | [Configuration](configuration.md) | `storage/milvus.py` |
| Chunk record | Row written to Milvus for a searchable unit. It carries namespace, connector URI, object URI, locator, content, dense/sparse vectors, chunk kind, metadata, and timestamp. | Milvus collection row | [Architecture](architecture.md) | `MilvusStore._build_schema` |
| Artifact kind | Named cache blob for one object. Current read paths use converted markdown, image/VLM text, and structured head cache. | `converted_md`, `vlm_text`, `head_cache` | [Architecture](architecture.md) | `Engine._put_artifact` |

## Errors

```json
{
  "code": "object_too_large_for_cat",
  "detail": "...",
  "suggestions": ["head", "cat --range", "export"]
}
```

| Term | Meaning | Use it with | Go next | Verified in |
|---|---|---|---|---|
| Error envelope | Runtime API error shape. Clients should switch on `code`, not parse `detail`. | CLI non-2xx output, SDK/API integrations | [HTTP API](api.md) | `ErrorResponse`, `_http_exc` |
| Canonical error code | Stable `/v1` error identifier documented by current protocol docs. | `not_found`, `validation_error`, `locator_not_found` | [Troubleshooting](troubleshooting.md) | `protocol/errors.md` |
| Suggestions | Short recovery hints returned with selected error codes and printed by the CLI as `try: ...`. | `object_too_large_for_cat`, `is_directory` | [Troubleshooting](troubleshooting.md) | `_CODE_SUGGESTIONS` |
