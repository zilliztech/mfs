# Troubleshooting

Use this page as a v0.4 runbook when MFS is reachable but a source does not
index, search, browse, or read the way you expect. The CLI commands here match
the current Rust `mfs` binary.

## First Commands

Start with the smallest ladder that separates endpoint, auth, job, indexing,
search, and browse failures:

```bash
mfs status
mfs config show
mfs job list
mfs connector list
```

| If you see | Run next | What it tells you |
|---|---|---|
| `mfs status` cannot connect | `mfs config show` | Shows the resolved endpoint, active profile, client id, and whether server info is reachable. |
| `mfs status` returns `401 [unauthorized]` | `mfs config show` and check the token source | The endpoint is reachable, but the bearer token sent by the CLI is missing or wrong. |
| `mfs add ...` returned a job id | `mfs job show JOB_ID` | Shows the current job status, error text, and object counts. |
| A job is stuck or should stop | `mfs job cancel JOB_ID` | Requests cancellation for a running or queued job. |
| A connector exists but indexing looks wrong | `mfs connector inspect TARGET` | Shows connector metadata plus object counts grouped by `search_status` and job counts grouped by status. |
| Search returns nothing | `mfs ls PATH --json` | Shows child paths plus `indexable` and per-entry `search_status`. |
| A search hit looks suspicious | `mfs head PATH -n 3` then `mfs cat PATH --range RANGE` | Verifies exact source content before trusting ranking. |
| You need a literal check | `mfs search QUERY PATH --mode keyword` | Removes semantic matching from the first pass. |
| The docs site fails | `uv run --group docs mkdocs build --strict` | Runs the verified MkDocs strict build from the repository root. |

!!! note "Useful links"
    For setup and upload basics, see [Quickstart](getting-started.md). For the
    search loop, see [Search and Browse](search-and-browse.md). For command
    names, see [CLI Reference](cli.md). For source setup, see
    [Connectors](connectors.md), [Deployment](deployment.md),
    [Configuration](configuration.md), and [HTTP API](api.md).
    For token and secret source lookup, see
    [Auth and Secrets](auth-and-secrets.md).
    For state backup, restore, and reset boundaries, see
    [Storage and Backup](storage-and-backup.md).
    For embedding, VLM, summary, and converter setup, see
    [Providers and Processing](providers.md). For local package setup and checks,
    see [Development](development.md). For a compact code matrix, see
    [Error Codes](errors.md).

## Endpoint and Auth

The CLI resolves its server endpoint in this order:

1. `MFS_API_URL`
2. the active profile in `$MFS_HOME/client.toml`
3. `http://127.0.0.1:13619`

The CLI resolves the bearer token in this order:

1. non-empty `MFS_API_TOKEN`
2. the active profile token in `$MFS_HOME/client.toml`
3. `$MFS_HOME/server.token`, for same-host local servers

Profile tokens may be literal values or `env:VAR` references. If an `env:VAR`
profile token resolves to an empty value, the CLI sends no bearer token from
that profile.

The server protects `/v1` with `Authorization: Bearer <token>` when
`auth_token` is configured. `mfs-server run` and `mfs-server api` ensure auth is
enabled by default: if no token is configured, the server reuses or creates
`$MFS_HOME/server.token`. Setting the server token to `-` is the explicit
opt-out for trusted or isolated networks. `GET /healthz` is exempt from bearer
auth.

| Symptom | Check | Recovery |
|---|---|---|
| `connection refused`, timeout, or DNS error | `mfs config show` | Fix `MFS_API_URL`, choose the intended profile, or start the server. |
| `401 [unauthorized]` | Confirm which token source should win | Export the intended `MFS_API_TOKEN`, update the active profile token, or read the local `server.token` on the server host. |
| Local CLI works but remote client fails | Compare `$MFS_HOME/server.token` on the server with the client token | Direct HTTP clients and remote CLIs must send `Authorization: Bearer <token>`; the local file fallback is CLI behavior only. |
| `mfs config show` says server unreachable | Check whether the endpoint is loopback, Docker, or a remote host | Use an endpoint reachable from the client process, not from the server process. |

!!! warning "Do not debug auth by pasting tokens into tickets"
    Use `mfs config show` for endpoint/profile visibility, but treat
    `MFS_API_TOKEN`, profile token values, and `server.token` as secrets.

For the same auth path as compact process and precedence tables, see
[Auth and Secrets](auth-and-secrets.md).

For direct HTTP integrations, the error envelope has this shape:

```json
{
  "code": "object_too_large_for_cat",
  "detail": "object_too_large_for_cat",
  "suggestions": ["head", "cat --range", "export"]
}
```

Clients should switch on `code`, not `detail`. The CLI prints the server
suggestions on non-2xx responses as a `try:` hint, so read the whole CLI error,
not only the first line. For the grouped code-to-recovery matrix, see
[Error Codes](errors.md).

## Upload or Shared Filesystem

For local paths, decide whether the server can read the same path directly.

| Situation | Command | Interpretation |
|---|---|---|
| CLI and server share the same filesystem path | `mfs add --no-upload --wait PATH` | Forces the server to read `PATH` directly. Use this for a real shared mount. |
| Server is in Docker, on another VM, or cannot read the client path | `mfs add --upload --wait PATH` | Scans the client tree, uploads changed files, and indexes the server-side staged copy. |
| You want to force upload even on the same host | `mfs add --upload --wait PATH` | Useful when the endpoint is local but the server process is isolated from the client path. |
| You need to resend every file | `mfs add --force-upload --wait PATH` | Resends all file bytes and forces a full re-index. |
| Bytes are already staged but you need a full re-index | `mfs add --force-index --wait PATH` | Re-indexes without forcing every file to be re-uploaded. |

The CLI auto-selects upload for an existing local path when it can compare the
server `machine_id` with the client hostname and they differ. `--upload` and
`--no-upload` override that decision.

After an upload, the connector identity is based on the stable client id and the
absolute client root. If a Docker server is exposed at `127.0.0.1:13619` and a
bare local path does not resolve for `search`, `ls`, or `cat`, list the
registered connector URI and use that URI directly:

```bash
mfs connector list
mfs connector inspect TARGET
```

Then scope search or browse to the displayed connector path.

## Docker Client/Server Networking

The provided compose file runs `mfs-server` bound to `0.0.0.0:13619`, exposes
host port `13619`, sets `MFS_HOME=/data`, and persists `/data` in the
`mfs-data` volume.

```bash
export MFS_API_URL=http://127.0.0.1:13619
export MFS_API_TOKEN="$(docker compose -f deployments/compose/docker-compose.yml exec -T mfs-server cat /data/server.token)"
mfs status
```

If you set `MFS_API_TOKEN` before `docker compose up`, the container uses that
known token instead of relying on the auto-generated `/data/server.token`.

| Symptom | Likely cause | Recovery |
|---|---|---|
| `mfs add --no-upload --wait ./repo` fails in Docker | The container cannot read the host path `./repo`. | Use `mfs add --upload --wait ./repo`, or mount a shared path into the container and pass the server-visible path. |
| Connector config uses `localhost` but the source is on the host | Inside the container, `localhost` is the container itself. | Use a hostname or address reachable from the server container. |
| Host CLI gets `401` against compose | The host token does not match the server token. | Set `MFS_API_TOKEN` to the compose token or restart compose with the intended `MFS_API_TOKEN`. |
| Search after upload cannot find the bare host path | The endpoint is loopback, so the CLI may not rewrite the path to the upload connector identity. | Use the connector URI shown by `mfs connector list` or `mfs connector inspect TARGET`. |

## Jobs and Indexing

`mfs add` queues work and returns a job id unless `--wait` is used. `--wait`
polls the job until it succeeds, fails, or is cancelled.
For the focused lifecycle guide, see [Jobs and Indexing Progress](jobs.md).

```bash
mfs add --upload --wait PATH
mfs add --no-upload --wait PATH
mfs job list
mfs job show JOB_ID
mfs job cancel JOB_ID
```

`mfs status` returns the current status envelope:

```json
{
  "connectors": [
    {"root_uri": "file://local/tmp/mfs-quickstart", "type": "file", "status": "active"}
  ],
  "jobs": {"queued": 1, "running": 1}
}
```

The `jobs` object is a count grouped by job status. The connector `status` is a
connector-row state such as `active` or `removing`; do not read it as per-object
search readiness.

`mfs job show JOB_ID` prints the full job object. The fields most useful for
triage are:

```json
{
  "id": "JOB_ID",
  "status": "failed",
  "error": "circuit_breaker_tripped",
  "total_objects": 120,
  "succeeded_objects": 34,
  "failed_objects": 5,
  "cancelled_objects": 81
}
```

| Job result | What to check | Recovery |
|---|---|---|
| `queued` or `running` for longer than expected | `mfs job show JOB_ID` | If the work should stop, run `mfs job cancel JOB_ID`; otherwise keep polling. |
| `failed` with a canonical error code | Error table below | Fix the root cause, then re-run `mfs add ...`. |
| `succeeded` but `succeeded_objects` is `0` | `mfs connector inspect TARGET` and `mfs ls PATH --json` | The connector found nothing indexable, the scope is wrong, or `[[objects]]` rules did not select text. |
| `failed_objects` is non-zero | `mfs job show JOB_ID` | Read `error`, then inspect the connector and a sample path. |
| `cancelled_objects` is non-zero | Confirm whether someone cancelled or removed the connector | Re-run add only after the operation that caused cancellation is finished. |

## Empty Search Results

Use keyword search and browse commands to separate ranking issues from indexing
or path-scope issues.

```bash
mfs connector inspect TARGET
mfs ls PATH --json
mfs head PATH -n 3
mfs search QUERY PATH --mode keyword
```

| Finding | Meaning | Next step |
|---|---|---|
| `mfs connector inspect TARGET` shows `object_count: 0` | The connector has no recorded objects. | Re-check the target URI, connector config, and latest job error. |
| `chunk_count: 0` but objects exist | Objects were recorded but no searchable chunks were produced. | Check `indexable`, file/media type, and `[[objects]] text_fields` for structured sources. |
| `mfs ls PATH --json` shows `search_status: null` | The entry is visible from the source but not present in MFS object metadata. | Re-run add, or confirm you are browsing the same path/connector that was indexed. |
| `search_status: not_indexed` | The object is known but has no chunks. | Use `mfs head PATH -n 3` or `mfs cat PATH --range RANGE` to see whether the object has readable text. |
| `search_status: partial` | Search can work, but recall may be incomplete. | Narrow the source, adjust verified connector object settings such as `chunk_max`, then re-run add. |
| `search_status: indexed` but keyword search misses an exact visible phrase | The query path may not match the connector identity, or the visible text is not in indexed content. | Search the connector URI shown by `mfs connector list`, then verify with `mfs cat PATH --range RANGE`. |

Protocol-level search availability values and current browse statuses are not
the same thing:

| Value set | Values | Scope |
|---|---|---|
| Protocol search availability | `available`, `partial`, `building`, `unavailable` | Describes whether search should be used for a source: ready, incomplete, still indexing, or unavailable. |
| Current `mfs ls PATH --json` entry status | `indexed`, `partial`, `not_indexed`, or `null` | Describes one listed entry from the objects table; `null` means the entry was seen by `ls` but has no object row. |

In the current v0.4 CLI, use `mfs status` for connector rows and job counts,
`mfs connector inspect TARGET` for object/job summaries for one connector, and
`mfs ls PATH --json` for per-entry search state.
For the identifier, locator, chunk-kind, and status vocabulary behind these
fields, see [Content Model](content-model.md).

## Read and Browse Errors

When search returns a source, verify exact content before using it:

```bash
mfs ls PATH --json
mfs head PATH -n 3
mfs cat PATH --range 1:80
```

| Symptom | Likely cause | Recovery |
|---|---|---|
| `cat` returns `is_directory` | The path is a directory. | Use `mfs ls PATH --json` or `mfs tree PATH -L 2`, then read a child object. |
| `cat` returns `object_too_large_for_cat` | Full read is guarded for a large object. | Use `mfs head PATH -n 20`, `mfs cat PATH --range 1:120`, or `mfs export PATH OUT`. |
| `cat --range` returns `range_unsupported` | The object type does not support ranged text reads. | Use `mfs cat PATH --meta` or `mfs export PATH OUT`. |
| `cat --peek` or `cat --skim` returns `density_unsupported` | Density views are unsupported for that object. | Use `mfs head PATH -n 20` or `mfs cat PATH --range 1:120`. |
| `tail` returns `tail_unsupported` | The object has no stable ordering for tail. | Use `mfs head PATH -n 20` or a bounded `cat --range`. |
| `cat --locator` returns `locator_not_found` | The structured record changed or the locator is stale. | Re-run search with `--json`, copy the current locator, then retry. |
| `not_found` | Path, object, connector, or job id does not match current server state. | Use `mfs connector list`, `mfs ls PATH --json`, or `mfs job list` to find the current identifier. |

## Connector Failures

Use the connector commands when the endpoint works but the source system does
not.

```bash
mfs connector list
mfs connector inspect TARGET
mfs connector probe TARGET --config ./connector.toml
mfs add TARGET --config ./connector.toml --wait
```

For non-local targets, `mfs add` runs a zero-billing estimate unless `--yes` is
set. The estimate reads metadata and uses a local chunker/tokenizer dry run; it
does not call the embedding API.

| Symptom | Check | Recovery |
|---|---|---|
| Probe fails | The server could not construct or connect the connector. | Fix connector credentials, config shape, optional server dependencies, or server-side network reachability. |
| Add fails with `since_unsupported` | The connector has no time cursor. | Drop `--since` and run a normal add. |
| Add fails with `field_missing` | A configured `text_field` is absent. | Fix the connector `[[objects]]` configuration, then re-run add. |
| Structured source indexes no text | `mfs ls PATH --json` and `mfs head PATH -n 3` | Ensure `[[objects]] text_fields` selects actual prose-like fields for that source. |
| Credentials use `env:VAR` but fail in Docker or service mode | The variable is not set in the server process environment. | Set the variable where `mfs-server` runs, not only in the client shell. |
| Credentials use `file:/abs/path` but fail | The server cannot read that absolute path. | Mount or place the secret file where the server process can read it. |

!!! warning "Connector config is server-side"
    The CLI parses TOML and sends it to the server, but credential references are
    resolved by the server when it constructs the connector. Make sure env vars,
    files, source network routes, and optional connector dependencies exist in
    the server environment.

## Canonical Error Codes

The complete code-to-recovery matrix lives in [Error Codes](errors.md). Keep
this page open for the runbooks that explain the next diagnostic step:

| Code family | Runbook here | Reference matrix |
|---|---|---|
| Endpoint, auth, malformed requests | [Endpoint and Auth](#endpoint-and-auth) | [Runtime and Request Codes](errors.md#runtime-and-request-codes) |
| Read, browse, range, density, locator failures | [Read and Browse Errors](#read-and-browse-errors) | [Read and Browse Codes](errors.md#read-and-browse-codes) |
| Jobs, sync conflicts, connector removal | [Jobs and Indexing](#jobs-and-indexing) | [Sync, Connector, and Upload Codes](errors.md#sync-connector-and-upload-codes) |
| Connector credentials and source reachability | [Connector Failures](#connector-failures) | [Sync, Connector, and Upload Codes](errors.md#sync-connector-and-upload-codes) |
| Embedding/provider failures and partial indexing | [Empty Search Results](#empty-search-results) plus [Providers and Processing](providers.md) | [Provider and Job Failure Codes](errors.md#provider-and-job-failure-codes) |

## Docs Build Failures

Run the docs build from the repository root:

```bash
uv run --group docs mkdocs build --strict
```

Strict mode is intentional. Broken links, missing pages, invalid admonitions, or
other MkDocs warnings should fail before publication.
