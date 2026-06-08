# CLI Reference

The Rust CLI is distributed as the `mfs` binary. It is a thin HTTP client over
the server's `/v1` API: parsing, upload packaging, output formatting, and
profile selection happen in the CLI; connectors, indexing, retrieval, storage,
and auth enforcement happen on the server.

Use this page when you know the task but need the exact command shape. For a
first local run, start with [Quickstart](getting-started.md). For the
search-to-read workflow, see [Search and Browse](search-and-browse.md).

## Global Behavior

| Concern | Current behavior |
|---|---|
| Binary name | `mfs` |
| Global JSON flag | `mfs --json <command> ...` |
| Default endpoint | `http://127.0.0.1:13619` |
| Client config | `$MFS_HOME/client.toml`, defaulting to `~/.mfs/client.toml` when `MFS_HOME` is unset |
| Local token fallback | `$MFS_HOME/server.token` |
| Error output | Non-2xx API responses print `error: <status> [<code>]: <detail>` and any server suggestions as `try: ...` |

Endpoint precedence:

1. `MFS_API_URL`
2. the active profile URL in `$MFS_HOME/client.toml`
3. `http://127.0.0.1:13619`

Bearer token precedence:

1. non-empty `MFS_API_TOKEN`
2. active profile token from `$MFS_HOME/client.toml`
3. `$MFS_HOME/server.token`

Profile tokens may be literal values or `env:VAR` references. If an `env:VAR`
profile token resolves to an empty value, the CLI sends no bearer token from
that profile.

For the server-side auth modes, CLI token precedence, and first `401` recovery
commands, see [Auth and Secrets](auth-and-secrets.md).

!!! note "HTTP API mapping"
    The CLI uses the same `/v1` endpoints documented in [HTTP API](api.md).
    Use `--json` when you need the raw response fields for automation.

## Command Matrix

| Task | Command | Primary API path |
|---|---|---|
| Check server, connectors, and jobs | `mfs status` | `GET /v1/status` |
| Add or sync a source | `mfs add TARGET` | `POST /v1/add`, or upload manifest endpoints for client-side upload |
| Search indexed content | `mfs search QUERY PATH` or `mfs search QUERY --all` | `GET /v1/search` |
| Search exact text | `mfs grep PATTERN PATH` | `GET /v1/grep` |
| List children | `mfs ls PATH` | `GET /v1/ls` |
| Walk a tree | `mfs tree PATH` | Repeated `GET /v1/ls` calls |
| Read content | `mfs cat PATH` | `GET /v1/cat` |
| Read starts or ends | `mfs head PATH`, `mfs tail PATH` | `GET /v1/head`, `GET /v1/tail` |
| Export full content | `mfs export PATH OUT` | `GET /v1/export` |
| Inspect jobs | `mfs job list`, `show`, `cancel` | `/v1/jobs` endpoints |
| Manage connectors | `mfs connector ...` | `/v1/connectors` plus `/v1/add` |
| Remove a connector | `mfs remove TARGET` | `DELETE /v1/connectors` |
| Manage endpoint profiles | `mfs profile ...` | Local `$MFS_HOME/client.toml` only |
| Show resolved client config | `mfs config show` | Local config plus `GET /v1/server/info` |
| Manage a local server process | `mfs serve ...` | Local process wrapper around `mfs-server run` |

## Status and Client Config

```bash
mfs status
mfs config show
```

`mfs status` prints the server status JSON envelope. It does not accept a path
or connector URI.

`mfs config show` prints:

- resolved endpoint;
- active profile name, or `(none)`;
- stable `client_id`;
- `server: ...` with `/v1/server/info`, or an unreachable message.

Use `mfs config show` before debugging auth or endpoint issues. For recovery
steps, see [Troubleshooting](troubleshooting.md).

## Add and Sync

`mfs add TARGET` registers or syncs a local path or connector URI. The command
always queues a job and returns immediately with a job id.

| Option | Meaning |
|---|---|
| `--config FILE` | Load connector configuration TOML and send it as the API `config` object. |
| `--since VALUE` | Send an incremental cursor or date for connectors with a time cursor. |
| `--force-index` | Force a full re-index by ignoring caches and fingerprints. |
| `--full` | Visible alias for `--force-index`. |
| `--upload` | Bundle and upload a local tree even when the endpoint is loopback. |
| `--force-upload` | Re-upload every file and force a full re-index. |
| `--no-upload` | Never upload; ask the server to read the target path directly. |
| `-y`, `--yes` | Skip the pre-flight estimate confirmation for external connectors. |

Local same-host example:

```bash
mfs add ./repo
```

Container or remote server example:

```bash
export MFS_API_URL=http://127.0.0.1:13619
export MFS_API_TOKEN="$(cat /path/to/server.token)"
mfs add --upload ./repo
```

External connector example:

```bash
mfs add postgres://prod-db --config ./postgres.toml
```

When `TARGET` is not an existing local path and `--yes` is not set, `mfs add`
first calls `/v1/connectors/estimate`, prints discovered objects plus estimated
chunks and tokens, and asks `Continue? [y/N]`. The estimate uses metadata plus
a local chunker/tokenizer dry run and does not make embedding API calls.

!!! note "Upload selection"
    For existing local paths, the CLI compares the server `machine_id` with the
    client hostname. If they differ, it uploads by default. `--upload` and
    `--no-upload` override that decision. See [Deployment](deployment.md) for
    Docker and Compose examples.

### Add Output

Human output is a queued job line:

```text
queued (job JOB_ID). Worker running in background -- run `mfs status` to check progress.
```

With `--json`, the queued output is:

```json
{"job_id":"JOB_ID"}
```

For job status meanings, worker behavior, and recovery steps, see
[Jobs and Indexing Progress](jobs.md).

## Search

`mfs search` requires either a scoped `PATH` or `--all`.

```bash
mfs search "rate limit handler" ./repo --top-k 10
mfs search "where is the retry budget documented" --all --top-k 20
```

| Option | Default | Meaning |
|---|---|---|
| `--all` | off | Search the whole namespace instead of one scoped path. |
| `--mode MODE` | `hybrid` | Search mode sent to the server: `hybrid`, `semantic`, or `keyword`. |
| `--top-k N` | `10` | Number of ranked candidates to request. |
| `--kind KINDS` | unset | Comma-separated chunk-kind filter such as `body,row_text`. |
| `--collapse` | off | Collapse multiple hits from the same source object. |

Human output prints one line per hit with source and score, followed by the
first 100 characters of snippet content when present.

Use JSON when you need locators:

```bash
mfs --json search "connector auth config" ./server --top-k 5
```

Search JSON includes fields such as `source`, `content`, `score`, `locator`,
and `metadata`. Feed `source` and `locator` back to `cat` before quoting or
editing behavior.

## Grep

`mfs grep PATTERN PATH` performs keyword/full-text search through the server.
The CLI sends `pattern` and `path` to `/v1/grep`.

```bash
mfs grep "MFS_API_TOKEN" ./server
mfs --json grep "Authorization: Bearer" ./docs
```

Human output prints:

```text
SOURCE: matching content preview
```

JSON output exposes match locators and the server's `via` field when present.

## Browse and Read

| Command | Options | Use it when |
|---|---|---|
| `mfs ls PATH` | global `--json` | You need children and per-entry metadata. |
| `mfs tree PATH` | `-L N`, `--depth N` default `2` | You need a bounded directory-style view. |
| `mfs cat PATH` | `--range`, `--meta`, `--locator`, `--peek`, `--skim` | You need exact content or metadata for one object. |
| `mfs head PATH` | `-n N`, `--lines N` default `20` | You need the first lines or entries. |
| `mfs tail PATH` | `-n N`, `--lines N` default `20` | You need the last lines or entries. |
| `mfs export PATH OUT` | none | You need full content written to a local file. |

Examples:

```bash
mfs ls ./repo
mfs tree ./repo -L 3
mfs cat ./repo/README.md --range 1:80
mfs cat postgres://prod/public/tickets/rows.jsonl --locator '{"id":12345}'
mfs head ./repo/README.md -n 20
mfs tail ./repo/logs/app.log --lines 80
mfs export ./repo/logs/app.log /tmp/app.log
```

`mfs cat --meta` prints the raw JSON response instead of only `content`.
`mfs cat --peek` sends `density=peek`; `mfs cat --skim` sends `density=skim`.

For large objects, prefer `head`, `tail`, `cat --range`, or `export` over bare
`cat`. See [Search and Browse](search-and-browse.md) for locator reopening
patterns.

## Jobs

```bash
mfs job list
mfs job show JOB_ID
mfs job cancel JOB_ID
```

| Command | Output |
|---|---|
| `mfs job list` | Human rows: status, operation kind, job id. With `--json`, raw job array. |
| `mfs job show JOB_ID` | Pretty JSON for one job. |
| `mfs job cancel JOB_ID` | `cancelled: true` or `cancelled: false`. |

Use job ids returned by `mfs add`, `mfs connector add`, or `mfs connector
update`.

## Connectors

Connector commands are the CLI entry point for source lifecycle tasks. For the
catalog and connector-specific TOML guidance, see [Connectors](connectors.md).

| Command | Meaning |
|---|---|
| `mfs connector probe TARGET --config FILE` | Try a connector without registering it. |
| `mfs connector add TARGET --config FILE` | Register and sync a connector through `/v1/add`. |
| `mfs connector update TARGET --config FILE` | Queue a sync with `update: true` and the new config. |
| `mfs connector list` | Print registered connector rows from `/v1/status`. |
| `mfs connector inspect TARGET` | Print the connector inspection JSON summary. |
| `mfs connector remove TARGET` | Remove a registered connector root after confirmation. |
| `mfs connector remove TARGET --yes` | Remove without confirmation. |
| `mfs remove TARGET` | Alias for `mfs connector remove TARGET`. |

Examples:

```bash
mfs connector probe web://docs --config ./web.toml
mfs connector add web://docs --config ./web.toml
mfs connector update web://docs --config ./web.toml
mfs connector list
mfs connector inspect web://docs
mfs connector remove web://docs
```

!!! warning "Connector removal"
    Removal deletes the connector's MFS-owned metadata, artifacts, and index
    entries. It does not delete data in the source system. Pass the registered
    connector root from `mfs connector list` or `mfs connector inspect`; child
    paths and unregistered targets are rejected.

## Profiles

Profiles are local CLI endpoint records in `$MFS_HOME/client.toml`.

```bash
mfs profile list
mfs profile add prod https://mfs.example.com --token env:MFS_API_TOKEN
mfs profile use prod
mfs config show
```

| Command | Meaning |
|---|---|
| `mfs profile list` | List profiles and mark the active one with `*`. |
| `mfs profile add NAME URL` | Add or update a profile. The first profile becomes active automatically. |
| `mfs profile add NAME URL --token TOKEN` | Store a literal bearer token or an `env:VAR` reference. |
| `mfs profile use NAME` | Set the active profile. |

If there are no profiles, `mfs profile list` prints the endpoint currently
resolved from `MFS_API_URL` or the default local endpoint.

## Local Server Wrapper

`mfs serve` manages a local `mfs-server run` process through a pid file and log
file under `$MFS_HOME`.

| Command | Meaning |
|---|---|
| `mfs serve start` | Spawn `mfs-server run --bind 127.0.0.1:13619`. |
| `mfs serve start --bind HOST:PORT` | Spawn the local server with a custom bind address. |
| `mfs serve stop` | Kill the pid recorded in `$MFS_HOME/server.pid`. |
| `mfs serve restart` | Stop then start. |
| `mfs serve restart --bind HOST:PORT` | Restart with a custom bind address. |
| `mfs serve status` | Report whether the recorded pid is alive. |
| `mfs serve logs` | Print the last 40 lines from `$MFS_HOME/server.log`. |

Use the source-run commands in [Quickstart](getting-started.md) or
[Deployment](deployment.md) when you need explicit `uv run mfs-server setup`
and `uv run mfs-server run` control.

## JSON and Errors

`--json` is global, but commands differ in how much custom formatting they do.
Use it for search, grep, list/tree, cat/head/tail, add queueing, and job lists
when another program needs raw fields.

The CLI parses server error envelopes:

```json
{
  "code": "object_too_large_for_cat",
  "detail": "...",
  "suggestions": ["head", "cat --range", "export"]
}
```

Human error output includes recovery hints:

```text
error: 400 Bad Request [object_too_large_for_cat]: ...
  try: head, cat --range, export
```

Clients should switch on `code`, not `detail`. See
[Troubleshooting](troubleshooting.md) and [HTTP API](api.md) for the canonical
error envelope and common recovery paths.

## Related Guides

| Guide | Use it for |
|---|---|
| [Quickstart](getting-started.md) | First local run and success checkpoints. |
| [Configuration](configuration.md) | Endpoint, token, profile, and server config precedence. |
| [Auth and Secrets](auth-and-secrets.md) | Server auth modes, CLI token precedence, connector credentials, and first auth recovery commands. |
| [Jobs and Indexing Progress](jobs.md) | Job ids, status counts, workers, and recovery. |
| [Search and Browse](search-and-browse.md) | Search, locate, and reopen exact evidence. |
| [Connectors](connectors.md) | Connector catalog, TOML config, credentials, and lifecycle. |
| [Deployment](deployment.md) | Source, Docker, Compose, and client/server upload mode. |
| [HTTP API](api.md) | Direct `/v1` requests and schema fields. |
| [Troubleshooting](troubleshooting.md) | Endpoint, auth, upload, indexing, and browse failures. |
