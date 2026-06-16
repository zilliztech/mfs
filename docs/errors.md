# Error Codes

Use this page when the CLI prints an HTTP error such as
`400 [object_too_large_for_cat]`, when an HTTP client receives an error
envelope, or when a job reports a canonical failure code.

!!! note "Switch on `code`, not `detail`"
    The runtime envelope has a stable `code`, human `detail`, and optional
    `suggestions`. Client code and SDK integrations should branch on `code`.
    Treat `detail` and `suggestions` as display text and recovery hints.

```json
{
  "code": "object_too_large_for_cat",
  "detail": "...",
  "suggestions": ["head", "cat --range", "export"]
}
```

The Rust CLI surfaces this envelope on non-2xx responses as:

```text
400 Bad Request [object_too_large_for_cat]: object_too_large_for_cat
  try: head, cat --range, export
```

`chunk_max_exceeded` is the exception to the normal HTTP-error pattern: it is
reported as partial search availability or `search_status: partial`, not as a
hard failed request.

## Triage Map

| If the code or status is about | Start with | Then open |
|---|---|---|
| Authentication or request shape | `mfs status`, `mfs config show`, or inspect the HTTP request body | [Troubleshooting](troubleshooting.md#endpoint-and-auth), [Auth and Secrets](auth-and-secrets.md), [HTTP API](api.md#errors) |
| A path, object, locator, or read operation | `mfs ls PATH --json`, `mfs head PATH -n 20`, or `mfs cat PATH --range A:B` | [Search and Browse](search-and-browse.md#error-recovery), [Troubleshooting](troubleshooting.md#read-and-browse-errors) |
| A queued or running ingest job | `mfs job list`, then `mfs job show JOB_ID` | [Jobs and Indexing Progress](jobs.md), [Troubleshooting](troubleshooting.md#jobs-and-indexing) |
| A connector source or credentials | `mfs connector inspect TARGET`, then `mfs connector probe TARGET --config FILE` | [Connectors](connectors.md) |
| Search availability is partial, building, or unavailable | `mfs ls PATH --json`, `mfs job list`, and bounded browse commands | [Search and Browse](search-and-browse.md#browse-when-search-is-weak), [Troubleshooting](troubleshooting.md#empty-search-results) |

## Runtime and Request Codes

These codes come from the API error wrapper, request validation handler, auth
middleware, and the canonical protocol table.

| Code | HTTP | Usually means | First action | Deeper guide |
|---|---:|---|---|---|
| `unauthorized` | 401 | Server auth is enabled and the bearer token is missing or wrong. | Check `MFS_API_TOKEN`, the active profile, or the local `server.token` fallback. | [Auth and Secrets](auth-and-secrets.md#first-failure-recovery) |
| `validation_error` | 422 | FastAPI or Pydantic rejected the request shape. | Fix the JSON body, query parameter, or field type. | [HTTP API: Errors](api.md#errors) |
| `bad_request` | 400 | A non-canonical 400 detail reached the runtime wrapper, such as malformed locator JSON or an empty upload body. | Re-check the command flags or HTTP request parameters. | [HTTP API](api.md) |
| `not_found` | 404 | Path, object, connector, or job id does not match current server state. | Use `mfs connector list`, `mfs ls PATH --json`, or `mfs job list` to find the current identifier. | [Troubleshooting](troubleshooting.md#first-commands) |
| `conflict` | 409 | A non-canonical conflict reached the runtime wrapper. | Inspect the in-flight job or retry after the conflicting operation finishes. | [Jobs and Indexing Progress](jobs.md) |
| `internal_error` | 500 | An uncaught server exception reached the fallback handler. | Capture the request, server logs, and current job or connector state before retrying. | [Server](server.md), [Troubleshooting](troubleshooting.md) |

## Read and Browse Codes

| Code | HTTP | Usually means | First action | Deeper guide |
|---|---:|---|---|---|
| `object_too_large_for_cat` | 400 | Full `cat` would read too much into one response. | Use `mfs head PATH -n 20`, `mfs cat PATH --range A:B`, or `mfs export PATH OUT`. | [Search and Browse: Error Recovery](search-and-browse.md#error-recovery) |
| `is_directory` | 400 | `cat`, `head`, `tail`, or `export` was requested for a directory/container. | Use `mfs ls PATH --json` or `mfs tree PATH -L 2`, then read a child object. | [Troubleshooting: Read and Browse Errors](troubleshooting.md#read-and-browse-errors) |
| `range_unsupported` | 400 | The object type cannot serve the requested range. | Use `mfs cat PATH --meta` or `mfs export PATH OUT`. | [Search and Browse](search-and-browse.md) |
| `density_unsupported` | 400 | `--peek`, `--skim`, or a density view was requested for an unsupported object. | Use `mfs head PATH -n 20` or `mfs cat PATH --range A:B`. | [Search and Browse: Error Recovery](search-and-browse.md#error-recovery) |
| `tail_unsupported` | 400 | The object has no stable ordering for tail reads. | Use `mfs head PATH -n 20` or a bounded `mfs cat PATH --range A:B`. | [Troubleshooting: Read and Browse Errors](troubleshooting.md#read-and-browse-errors) |
| `locator_not_found` | 404 | The structured record or text range locator is no longer present. | Re-run search with `--json`, copy the current locator, then retry `cat --locator`. | [Search and Browse: Locate Exact Hits](search-and-browse.md#locate-exact-hits) |

## Sync, Connector, and Upload Codes

| Code | HTTP | Usually means | First action | Deeper guide |
|---|---:|---|---|---|
| `since_unsupported` | 400 | `--since` was used with a connector that does not support a time cursor. | Drop `--since` or use a connector-specific cursor only where documented. | [Connectors](connectors.md) |
| `sync_already_running` | 409 | A sync job is already in flight for that connector. | Run `mfs job list`, then wait or run `mfs job cancel JOB_ID` if the job should stop. | [Jobs and Indexing Progress](jobs.md) |
| `connector_removing` | 409 | The connector is being removed and cannot start new work. | Wait for removal to finish, then retry. | [Connectors](connectors.md) |
| `connector_unhealthy` | 502 | The source is unreachable or credentials/connectivity failed. | Check server-side credentials and network reachability, then probe the connector. | [Connectors](connectors.md), [Troubleshooting: Connector Failures](troubleshooting.md#connector-failures) |
| `field_missing` | 400 | A configured structured-source text field is absent. | Fix the connector `[[objects]]` config, then re-run add or update. | [Connectors](connectors.md) |
| `upload_rejected` | 400 | Upload was required by topology, but upload was disabled. | Adjust the path mode or profile; for isolated servers use `mfs add --upload PATH`. | [Troubleshooting: Upload or Shared Filesystem](troubleshooting.md#upload-or-shared-filesystem) |
| `upload_not_applicable` | 400 | Upload was forced where shared-filesystem mode applies. | Adjust upload flags or profile for the current topology. | [Troubleshooting: Upload or Shared Filesystem](troubleshooting.md#upload-or-shared-filesystem) |

## Provider and Job Failure Codes

These codes may appear as immediate API errors, CLI errors, or job error fields
depending on when the failure is detected.

| Code | HTTP | Usually means | First action | Deeper guide |
|---|---:|---|---|---|
| `embedding_quota_exceeded` | 502 | The embedding provider is out of quota. | Fix quota or billing, then re-run `mfs add ...`. | [Providers and Processing](providers.md), [Jobs and Indexing Progress](jobs.md) |
| `embedding_auth_failed` | 502 | The embedding provider key is missing or invalid. | Fix the provider key in the server environment or config, then re-run `mfs add ...`. | [Providers and Processing](providers.md) |
| `circuit_breaker_tripped` | 502 | Too many consecutive fatal or exhausted object failures caused the job to abort. | Fix the root cause shown by `mfs job show JOB_ID`, then re-run add. | [Troubleshooting: Jobs and Indexing](troubleshooting.md#jobs-and-indexing) |

## Partial Search Status

`chunk_max_exceeded` is recorded as incomplete indexing rather than a normal
HTTP error. Search can still return results, but recall may be incomplete.

| Status surface | Values | What to do |
|---|---|---|
| Source-level availability | `available`, `partial`, `building`, `unavailable` | Use search when available, verify more carefully when partial, browse while building, and use browse/grep when unavailable. |
| `mfs ls PATH --json` entry status | `indexed`, `partial`, `not_indexed`, or `null` | Treat `partial` as searchable but incomplete; treat `not_indexed` or `null` as browse-first. |
| Canonical partial code | `chunk_max_exceeded` | Narrow the source or adjust verified connector object settings such as `chunk_max`, then re-run add. |

When search availability is partial, verify with exact browse commands before
using a result:

```bash
mfs search "release checklist" PATH --top-k 25 --json
mfs grep "release checklist" PATH --json
mfs cat SOURCE --range A:B
```

Use [Search and Browse](search-and-browse.md#browse-when-search-is-weak)
for the mental model and [Troubleshooting](troubleshooting.md#empty-search-results)
for recovery steps.
