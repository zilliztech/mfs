# FAQ

## Which page should I open first?

| Question | Start here |
|---|---|
| Is MFS a fit for my task? | [Why MFS](why.md) |
| How do I run it once on a small folder? | [Quickstart](getting-started.md) |
| Which command shape is current? | [CLI Reference](cli.md) |
| How do I add a non-file source? | [Connectors](connectors.md) |
| How do I call MFS without shelling out? | [HTTP API](api.md) and [SDKs](sdks.md) |
| Something connects but does not search, browse, or index correctly. | [Troubleshooting](troubleshooting.md) |

## What is the shortest first run?

Install the published `mfs` CLI, then run the Python server from source during
the v0.4 beta:

```bash
cd mfs/server/python
uv sync
uv run mfs-server setup
uv run mfs-server run
```

In another terminal, verify the server, add a small folder, search, and reopen
the exact source:

```bash
mfs status
mfs add --wait /path/to/folder
mfs search "your query" /path/to/folder --top-k 5
mfs cat /path/to/folder/file.md --range 1:80
```

Use [Quickstart](getting-started.md) for the full checkpoint-driven runbook.

## Is the server required?

Yes for v0.4. The Rust CLI named `mfs` is a thin HTTP client over the Python
FastAPI server named `mfs-server`. The API lives under `/v1`.

The CLI default endpoint is `http://127.0.0.1:13619`. `mfs-server run` and
`mfs-server api` bind to `127.0.0.1:13619` by default.

## Is MFS a filesystem?

No. MFS exposes file-like commands over registered sources, but it does not
mount a POSIX filesystem and does not provide filesystem write or lock
semantics. The original source stays the source of truth.

## Is MFS a vector database replacement?

No. MFS may use Milvus or Zilliz as part of its own indexing backend, but MFS is
a search, browse, and read layer over source systems. If your application needs
a vector database API, use that database directly.

## How do auth and tokens work?

`GET /healthz` is unauthenticated. `/v1` endpoints require
`Authorization: Bearer <token>` when server auth is configured.

`mfs-server run` and `mfs-server api` enable auth by default by reusing or
creating `$MFS_HOME/server.token` unless configured otherwise. A same-host CLI
can read that token automatically. Remote CLIs and direct HTTP clients should
send the intended bearer token.

CLI token precedence is:

1. `MFS_API_TOKEN`
2. active profile token from `$MFS_HOME/client.toml`
3. `$MFS_HOME/server.token`

Use [CLI Reference](cli.md), [Deployment](deployment.md), and
[Troubleshooting](troubleshooting.md) for profile, Docker, and remote-client
examples.

## Do I need upload mode for Docker or a remote server?

Use upload mode when the server process cannot read the client path:

```bash
mfs add --upload --wait /path/on/client
```

Use shared-filesystem mode only when the server can read the same path:

```bash
mfs add --no-upload --wait /shared/path
```

For Docker and Compose all-in-one servers, host paths are usually not visible
inside the container unless mounted. See [Deployment](deployment.md) for the
topology rules.

## Which connector schemes are built in?

The server registry imports `file` directly and attempts to lazy-import these
optional built-ins:

```text
web, github, postgres, mysql, mongo, slack, discord, gmail, notion, jira,
linear, zendesk, salesforce, hubspot, bigquery, snowflake, s3, gdrive, feishu
```

If an optional connector's dependencies are not installed in the server
environment, that scheme is skipped there. Use
`mfs connector probe TARGET --config FILE` before relying on a connector, and
use `mfs connector list` to list registered connectors.

## Should I use search, grep, or browse?

| Need | Use |
|---|---|
| Conceptual or paraphrased query | `mfs search QUERY PATH` |
| Exact token or identifier | `mfs grep PATTERN PATH` |
| Whole namespace search | `mfs search QUERY --all` |
| Directory orientation | `mfs ls PATH` or `mfs tree PATH -L N` |
| Exact evidence | `mfs cat`, `mfs head`, `mfs tail`, or `mfs export` |

Search and grep results are candidates. Reopen the returned `source` with
`cat --range` or `cat --locator` before relying on the content.

## Can I search without a path?

Only when you intentionally search all registered sources:

```bash
mfs search "release checklist" --all --top-k 10
```

Prefer a scoped path or URI when you know it:

```bash
mfs search "release checklist" ./docs --top-k 10
```

Scoped search is easier to verify and usually produces less noise.

## Can I use the API or SDKs instead of the CLI?

Yes. The HTTP API is the `/v1` control plane documented in
[HTTP API](api.md), and the OpenAPI source is `protocol/openapi.yaml`.

Generated Python and TypeScript SDKs are checked in under `sdks/`, but the
current generated clients do not cover every OpenAPI operation. Set the base URL
to the running server, send bearer auth when required, and call the HTTP API
directly for operations missing from the generated clients.

## What deployment shape should I use?

| Shape | v0.4 status |
|---|---|
| Source `mfs-server run` | Supported beta path for local evaluation and development. |
| Docker all-in-one | Runnable v0.4 topology. |
| Docker Compose all-in-one | Runnable v0.4 topology around the same server image. |
| Helm api/worker | Rendered post-v0.4 direction, not the default runnable v0.4 deployment. |

During the v0.4 beta, the published artifact is the CLI. The server runs from
source or from a locally built Docker/Compose image.

## How do I troubleshoot a confusing result?

Start with the ladder that separates endpoint, auth, job, indexing, and browse
state:

```bash
mfs status
mfs config show
mfs job list
mfs connector list
mfs ls PATH --json
```

If a search hit looks right, verify it with:

```bash
mfs cat SOURCE --range A:B
```

If structured search returns a locator, copy the locator JSON exactly:

```bash
mfs cat SOURCE --locator '{"id":12345}'
```

Use [Troubleshooting](troubleshooting.md) for error codes and recovery commands.
