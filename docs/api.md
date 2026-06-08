# HTTP API

The MFS HTTP API is the `/v1` control plane between the CLI, generated SDKs,
and the Python FastAPI server. Use it when you want to integrate directly with
MFS without shelling out to `mfs`.

`protocol/openapi.yaml` is the source of truth for endpoint paths, methods,
operation IDs, and typed schemas. `protocol/schemas/openapi.json` mirrors the
same contract for JSON consumers. Server behavior such as authentication,
runtime error wrapping, and a few query-parameter details lives in
`server/python/src/mfs_server/api/app.py`.
For local protocol-change checks and SDK regeneration steps, see
[Development](development.md#openapi-to-sdks).

The examples below use `MFS_URL` so they work with local, Docker, and remote
servers:

```bash
export MFS_URL=http://127.0.0.1:13619
```

## Authentication

When the server is configured with `auth_token`, every request except
`GET /healthz` must include a bearer token:

```bash
export MFS_TOKEN=replace-with-your-token
curl -sS -H "Authorization: Bearer $MFS_TOKEN" "$MFS_URL/v1/server/info"
```

If `auth_token` is not configured on the server, the bearer header is not
required. Do not infer auth behavior from generated SDK README text; the
server middleware is authoritative.

`GET /healthz` is intentionally outside `/v1` and is exempt from bearer auth so
liveness probes can run without access to API credentials. It returns only:

```json
{ "status": "ok" }
```

!!! note "CLI token fallback"
    The Rust CLI can read `MFS_API_TOKEN`, profile tokens, or the local
    `server.token` file. That fallback is CLI behavior, not an HTTP API
    requirement. Direct API clients should send `Authorization: Bearer <token>`
    whenever their target server requires it.

For the full server, CLI, and direct-client token map, see
[Auth and Secrets](auth-and-secrets.md#cli-and-api-token-sources).

## Workflow Matrix

| Workflow | Endpoint | Operation ID | Request shape | Response shape |
|---|---|---|---|---|
| Server info | `GET /v1/server/info` | `getServerInfo` | No body | `ServerInfo` |
| Server status | `GET /v1/status` | `status` | No body | `StatusResponse` |
| Add or sync a source | `POST /v1/add` | `addSource` | `AddRequest` JSON | `AddResponse` |
| Upload a tar stream | `POST /v1/upload?name=...&process=...` | `uploadSource` | Raw tar or tar.gz body | `AddResponse` |
| File manifest step | `POST /v1/files/manifest` | `filesManifest` | `ManifestRequest` JSON | `ManifestResponse` |
| File upload step | `PUT /v1/files/upload?client_id=...&root=...&process=...&full=...` | `filesUpload` | Raw tar or tar.gz body | `AddResponse` |
| List jobs | `GET /v1/jobs?limit=...` | `listJobs` | Query parameters | `JobResponse[]` |
| Poll one job | `GET /v1/jobs/{job_id}` | `getJob` | Path parameter | `JobResponse` |
| Cancel a job | `POST /v1/jobs/{job_id}/cancel` | `cancelJob` | Path parameter | `CancelResponse` |
| Probe a connector | `POST /v1/connectors/probe` | `probeConnector` | `ProbeRequest` JSON | `ProbeResponse` |
| Estimate a connector | `POST /v1/connectors/estimate` | `estimateConnector` | `ProbeRequest` JSON | `EstimateResponse` |
| Inspect a connector | `GET /v1/connectors/inspect?target=...` | `inspectConnector` | Query parameter | JSON summary |
| Remove a connector | `DELETE /v1/connectors?target=...` | `removeConnector` | Query parameter | `RemoveResponse` |
| Search indexed content | `GET /v1/search?q=...` | `search` | Query parameters | `SearchResponse` |
| Grep a path | `GET /v1/grep?pattern=...&path=...` | `grep` | Query parameters | `GrepResponse` |
| List a path | `GET /v1/ls?path=...` | `ls` | Query parameter | `LsResponse` |
| Read an object | `GET /v1/cat?path=...` | `cat` | Query parameters | `CatResponse` or metadata |
| Read the first entries | `GET /v1/head?path=...&n=...` | `head` | Query parameters | `CatResponse` |
| Read the last entries | `GET /v1/tail?path=...&n=...` | `tail` | Query parameters | `CatResponse` |
| Export full content | `GET /v1/export?path=...` | `export` | Query parameter | `CatResponse` |

`GET /v1/connectors/inspect` currently has an empty response schema in
OpenAPI. Treat it as a connector/server JSON summary and avoid depending on
fields that are not modeled in the protocol.

## Minimal Examples

### Check server info

```bash
curl -sS -H "Authorization: Bearer $MFS_TOKEN" \
  "$MFS_URL/v1/server/info"
```

```json
{
  "version": "0.4.0",
  "machine_id": "mfs-host",
  "namespace": "default"
}
```

### Add a source and poll the job

Set `process=false` when you want the request to return a `job_id` for worker
processing. Set `process=true` when your client wants the server call to run
indexing inline before returning.

```bash
curl -sS -H "Authorization: Bearer $MFS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"target":"/data/project","process":false}' \
  "$MFS_URL/v1/add"
```

```json
{ "job_id": "8a6c6d4e3f9a4d1bb7d2b7c3f0e1a234" }
```

Poll the job by ID:

```bash
curl -sS -H "Authorization: Bearer $MFS_TOKEN" \
  "$MFS_URL/v1/jobs/8a6c6d4e3f9a4d1bb7d2b7c3f0e1a234"
```

```json
{
  "id": "8a6c6d4e3f9a4d1bb7d2b7c3f0e1a234",
  "status": "succeeded",
  "op_kind": "sync",
  "trigger": "manual",
  "error": null,
  "total_objects": 42,
  "succeeded_objects": 42,
  "failed_objects": 0,
  "cancelled_objects": 0,
  "started_at": "2026-06-03T10:00:00Z",
  "finished_at": "2026-06-03T10:00:15Z"
}
```

The API models `status` as a string. Clients should display unknown status
values instead of hard-coding a closed enum.

For queued versus inline processing, worker modes, and status recovery, see
[Jobs and Indexing Progress](jobs.md).

### Search

`q` is required. `path`, `mode`, `top_k`, and `collapse` are optional in
OpenAPI. The server also accepts `kind` as a comma-separated chunk-kind filter.

```bash
curl -G -sS -H "Authorization: Bearer $MFS_TOKEN" \
  "$MFS_URL/v1/search" \
  --data-urlencode "q=release checklist" \
  --data-urlencode "path=/data/project" \
  --data-urlencode "top_k=5"
```

```json
{
  "results": [
    {
      "source": "/data/project/README.md",
      "content": "Release checklist...",
      "score": 0.82,
      "locator": { "lines": [10, 18] },
      "metadata": { "chunk_kind": "body" }
    }
  ]
}
```

### Browse and read

List a path:

```bash
curl -G -sS -H "Authorization: Bearer $MFS_TOKEN" \
  "$MFS_URL/v1/ls" \
  --data-urlencode "path=/data/project"
```

Read a bounded range:

```bash
curl -G -sS -H "Authorization: Bearer $MFS_TOKEN" \
  "$MFS_URL/v1/cat" \
  --data-urlencode "path=/data/project/README.md" \
  --data-urlencode "range=1:120"
```

Read by locator when a search hit includes one:

```bash
curl -G -sS -H "Authorization: Bearer $MFS_TOKEN" \
  "$MFS_URL/v1/cat" \
  --data-urlencode "path=/data/project/README.md" \
  --data-urlencode 'locator={"lines":[10,18]}'
```

Use `meta=true` when you need object metadata instead of content:

```bash
curl -G -sS -H "Authorization: Bearer $MFS_TOKEN" \
  "$MFS_URL/v1/cat" \
  --data-urlencode "path=/data/project/README.md" \
  --data-urlencode "meta=true"
```

## Key Schemas

### AddRequest

| Field | Type | Notes |
|---|---|---|
| `target` | string | Required path or connector URI to register and index. |
| `config` | object or null | Optional connector configuration. The CLI loads this from a TOML file, but API clients send JSON. |
| `full` | boolean | Force full re-indexing and ignore caches or fingerprints. |
| `since` | string or null | Cursor or date for connectors that support incremental sync. |
| `process` | boolean | Controls inline processing versus worker processing. Send it explicitly if your client depends on either behavior. |
| `update` | boolean | Apply config to an existing connector. |

### ResultEnvelope

| Field | Type | Notes |
|---|---|---|
| `source` | string | Object URI or path that can be sent to browse endpoints. |
| `content` | string | Snippet or matched content. |
| `score` | number or null | Ranking score when available. |
| `locator` | object or null | Per-hit identity, such as line bounds or a structured connector key. |
| `metadata` | object | Connector and chunk metadata. |

### LsEntry

| Field | Type | Notes |
|---|---|---|
| `name` | string | Entry name. |
| `type` | string | `file` or `dir`. |
| `media_type` | string or null | Media type when known. |
| `size_hint` | integer or null | Approximate or known size hint. |
| `path` | string or null | Full object URI or path for `cat`, `search`, `head`, or `export`. |
| `search_status` | string or null | Indexing status such as `indexed`, `partial`, or `not_indexed` when known. |
| `indexable` | boolean or null | Whether the object is eligible for indexing. |

### JobResponse

| Field | Type | Notes |
|---|---|---|
| `id` | string | Job ID returned by ingest endpoints. |
| `status` | string | Current job status. Treat as an open string. |
| `op_kind` | string or null | Operation kind when stored. |
| `trigger` | string or null | Trigger source when stored. |
| `error` | string or null | Error text or code when the job fails. |
| `total_objects` | integer or null | Total objects planned or observed. |
| `succeeded_objects` | integer or null | Object count completed successfully. |
| `failed_objects` | integer or null | Object count that failed. |
| `cancelled_objects` | integer or null | Object count cancelled. |
| `started_at` | string or null | Start timestamp when available. |
| `finished_at` | string or null | Finish timestamp when available. |

### StatusResponse

| Field | Type | Notes |
|---|---|---|
| `connectors` | `ConnectorRow[]` | Registered connectors with `root_uri`, `type`, and `status`. |
| `jobs` | object | Counts grouped by job status. |

### Connector Requests and Responses

| Schema | Fields | Notes |
|---|---|---|
| `ProbeRequest` | `target`, `config` | Used by probe and estimate endpoints. |
| `ProbeResponse` | `target`, `type`, `ok`, `detail` | Reports whether the server can probe the connector target. |
| `EstimateResponse` | `target`, `type`, `objects`, `sampled_objects`, `est_chunks`, `est_tokens` | Zero-billing pre-flight estimate based on metadata and local dry-run work. |
| `RemoveResponse` | `target`, `removed` | Reports whether connector removal was applied. |

## Errors

Runtime API errors use a stable envelope:

```json
{
  "code": "object_too_large_for_cat",
  "detail": "...",
  "suggestions": ["head", "cat --range", "export"]
}
```

Clients should switch on `code`, not `detail`. The server wraps
`HTTPException`, request-validation failures, and uncaught exceptions into this
shape. Some OpenAPI responses still reference FastAPI validation schemas, but
the runtime validation handler returns:

```json
{
  "code": "validation_error",
  "detail": "...",
  "suggestions": ["fix request shape"]
}
```

Common codes include:

| Code | HTTP status | Typical cause |
|---|---|---|
| `unauthorized` | 401 | Missing or invalid bearer token when auth is enabled. |
| `validation_error` | 422 | Malformed request shape, invalid enum value, or unknown query parameter. |
| `not_found` | 404 | Missing path, object, connector, or job. |
| `sync_already_running` | 409 | A sync is already in flight for the connector. |
| `connector_removing` | 409 | Connector removal is already in progress. |
| `object_too_large_for_cat` | 400 | `cat` without a bounded range on a large object. |
| `is_directory` | 400 | `cat` was requested for a directory. |
| `range_unsupported` | 400 | Range reads are not supported for the object. |
| `density_unsupported` | 400 | Requested density is unsupported for the object. |
| `tail_unsupported` | 400 | The object has no stable ordering for tail reads. |
| `locator_not_found` | 404 | The requested locator is no longer present. |
| `connector_unhealthy` | 502 | Source connectivity or credentials failed. |
| `internal_error` | 500 | Unhandled server exception. |

`chunk_max_exceeded` is not a hard error. It is surfaced through partial search
availability or `search_status: partial`, so search may still return results
with incomplete recall.

For the full code-to-recovery matrix, including CLI first actions and deeper
workflow links, see [Error Codes](errors.md).

## SDKs

Python and TypeScript SDK source trees are generated from
`protocol/openapi.yaml` by `sdks/generate.sh`:

| Language | Directory | Generator |
|---|---|---|
| Python | `sdks/python/` | OpenAPI Generator `python` client with `urllib3` |
| TypeScript | `sdks/typescript/` | OpenAPI Generator `typescript-fetch` client |

The checked-in SDK directories currently expose generated clients for the
server, ingest, retrieval, and browse API groups. The OpenAPI spec also contains
connector-management operations; after protocol changes, regenerate the SDKs and
check the generated API classes before documenting a client method name.

Treat generated README install, authorization, and default-host snippets as
scaffolding unless your release process verifies them. For the docs-site SDK
entry point, see [SDKs](sdks.md).

## Related Pages

| Need | Page |
|---|---|
| Search, grep, ls, and cat workflow | [Search and Browse](search-and-browse.md) |
| Result envelopes, locators, chunk kinds, and search status vocabulary | [Content Model](content-model.md) |
| Job lifecycle and indexing progress | [Jobs and Indexing Progress](jobs.md) |
| Connector targets and config shapes | [Connectors](connectors.md) |
| CLI command surface | [CLI Reference](cli.md) |
| Auth token sources and health-check boundary | [Auth and Secrets](auth-and-secrets.md) |
| Generated clients | [SDKs](sdks.md) |
| Protocol-change checks and SDK regeneration | [Development](development.md#openapi-to-sdks) |
| Runtime and integration issues | [Troubleshooting](troubleshooting.md) |
