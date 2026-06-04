# Status and Limits

This page is a conservative status entry point for the MFS v0.4 beta. It is not
a product roadmap and does not promise release dates, support policy, future
connectors, or stable API fields. Use it to decide what is runnable today, what
is still beta-only, and where to click next.

!!! warning "Beta boundary"
    Treat the v0.4 line as evaluation software. Pin the CLI version, run the
    server from the same repository checkout you are testing, and verify API or
    SDK integrations against the generated contract plus the running server.

## Current shape

| Area | Current status | Next page |
|---|---|---|
| Architecture | v0.4 is a client/server system: the Rust CLI is `mfs`, the Python FastAPI server is `mfs-server`, and clients call the HTTP `/v1` control plane. | [Architecture](architecture.md) |
| First run | The documented beta path is to install the published CLI and run the Python server from source. Local defaults use `127.0.0.1:13619`, `$MFS_HOME`, ONNX embeddings, Milvus Lite, SQLite, local artifact cache, and an auto-generated bearer token. | [Quickstart](getting-started.md) |
| Configuration | Server config comes from `--config`, `MFS_SERVER_CONFIG`, local TOML files, then built-in defaults. CLI endpoint and token resolution are separate client-side rules. | [Configuration](configuration.md) |
| API | `protocol/openapi.yaml` is the endpoint and schema source of truth, while runtime auth and error-envelope behavior live in the FastAPI app. | [HTTP API](api.md) |
| SDKs | Python and TypeScript clients are generated from OpenAPI and checked in under `sdks/`, but generated-client coverage is not the full `/v1` surface. | [SDKs](sdks.md) |
| Deployment | Source, Docker all-in-one, and Compose all-in-one are the runnable v0.4 shapes. The Helm api/worker chart is a rendered post-v0.4 direction. | [Deployment](deployment.md) |
| Connectors | The `file` connector is always imported. Other built-in schemes depend on optional server extras and should be probed in the target server environment. | [Connectors](connectors.md) |

## Runnable artifacts

| Artifact or shape | What is available now | Limit to keep in mind |
|---|---|---|
| Rust CLI `mfs` | The public docs install `mfs-cli` as the `mfs` binary from the v0.4 beta release line. | Keep command examples aligned with the current [CLI Reference](cli.md); some repository README snippets still use older command forms. |
| Python server `mfs-server` | Run from `server/python` with `uv sync`, `uv run mfs-server setup`, and `uv run mfs-server run`. | The top-level README states the server is not published to PyPI for the documented beta path. |
| Docker all-in-one | Build the server image from the repository Dockerfile and mount `/data` for server state. | Host paths are not automatically visible inside the container; use upload mode unless the path is mounted and passed as a server-visible path. |
| Docker Compose all-in-one | The Compose wrapper starts the same all-in-one server, exposes port `13619`, and persists `/data`. | Read the generated or configured API token from the container before using the host CLI. |
| Helm api/worker | The chart renders API and worker deployments plus an API service. | The deployment docs describe this as a post-v0.4 direction, not the runnable v0.4 default. |
| Generated SDK source | Python and TypeScript SDK source trees are generated from `protocol/openapi.yaml`. | Treat generated README install, auth, and default-host snippets as scaffolding unless the release process verifies them. |

## Stability boundaries

### API and auth

- `/v1` is the integration surface for the CLI, direct HTTP clients, and
  generated SDKs.
- When the server has `auth_token` configured, every request except
  `GET /healthz` must send `Authorization: Bearer <token>`.
- `GET /healthz` is intentionally unauthenticated and returns only process
  health.
- Runtime API errors use a `{code, detail, suggestions}` envelope; clients
  should switch on `code`.
- `GET /v1/connectors/inspect` currently has an empty 200 response schema in
  OpenAPI. Treat it as a server JSON summary and avoid depending on undocumented
  fields.

### SDKs

- The checked-in generated clients expose the common server, ingest, retrieval,
  and browse groups.
- Connector management, file manifest/upload steps, `head`, `tail`, `export`,
  and `listJobs` are part of the OpenAPI surface but are not all exposed as
  generated SDK methods in the current checked-in clients.
- Generated SDK READMEs and generated API docs say authorization is not required
  because the OpenAPI spec does not model bearer auth. The running server is
  authoritative for auth behavior.
- Generated TypeScript runtime defaults to `http://127.0.0.1:8765`; the
  documented `mfs-server run` and `mfs-server api` default bind is
  `127.0.0.1:13619`. Set the base URL explicitly.

### Deployment

- Persist `$MFS_HOME` for source runs and `/data` for containers. This keeps the
  server config, generated token, SQLite metadata, Milvus Lite data, caches, and
  ONNX model cache across restarts.
- Use normal path mode only when the server can read the same path the CLI
  passes. Use `mfs add --upload` for Docker, remote servers, or any split where
  the server cannot read the client path.
- Current Compose and Helm assets mention `MFS_MILVUS_URI` and
  `MFS_MILVUS_TOKEN`, while the server configuration docs identify
  `MILVUS_URI`, `MILVUS_TOKEN`, `ZILLIZ_URI`, `ZILLIZ_TOKEN`, and
  `ZILLIZ_API_KEY` as the runtime names read by server code. Use the verified
  runtime names or `server.toml` until deployment assets and runtime config are
  aligned.

### Connectors

- A connector scheme listed in the docs means the codebase includes that
  connector, not that every server environment has the optional dependency
  installed.
- Probe the target server before relying on a non-file connector:

```bash
mfs connector probe TARGET --config ./connector.toml
```

- Connector credential references such as `env:VAR` and `file:/abs/path` are
  resolved by the server, not by the client shell. Put secrets and readable
  files in the server environment.

## Known docs and source mismatches

| Mismatch | What to do |
|---|---|
| The top-level `README.md` and `cli/README.md` still include older quickstart commands such as `mfs status file://...`, `--connector-uri`, and `mfs connector ls`. | Follow [Quickstart](getting-started.md), [CLI Reference](cli.md), [Connectors](connectors.md), and [Troubleshooting](troubleshooting.md) for current v0.4 command forms such as `mfs add --wait`, `mfs search QUERY PATH`, `mfs connector list`, and `mfs job show`. |
| Beta labels are not uniform across repository snippets: the docs install the CLI from `v0.4.0-beta.2`, server and SDK package metadata in the repo show `0.4.0-beta.3`, and some deployment README examples still use `0.4.0-beta.1` image tags. | Treat the repository as a v0.4 beta checkout and pin the exact artifact you test. Do not infer a single published server or SDK artifact from mixed example tags. |
| OpenAPI does not model bearer auth, so generated SDK docs say no authorization is required. | Send `Authorization: Bearer <token>` whenever the target server has auth enabled; use [HTTP API](api.md) and [SDKs](sdks.md) for integration examples. |
| `GET /v1/connectors/inspect` has an empty OpenAPI response schema. | Use it for human/operator inspection, but avoid scripting against fields that are not modeled. |
| Compose and Helm deployment assets render or set `MFS_MILVUS_*`, but the server runtime docs point to `MILVUS_*` and `ZILLIZ_*` names. | Use [Configuration](configuration.md) and [Deployment](deployment.md) for the currently documented runtime names. |

## Where to go next

| If you need to... | Start with |
|---|---|
| Run MFS for the first time | [Quickstart](getting-started.md) |
| Decide whether MFS fits your use case | [Why MFS](why.md) and [FAQ](faq.md) |
| Understand the client/server model | [Architecture](architecture.md) and [Server](server.md) |
| Search, browse, and verify exact content | [Search and Browse](search-and-browse.md) |
| Check command names and flags | [CLI Reference](cli.md) |
| Configure backends, auth, and environment variables | [Configuration](configuration.md) |
| Deploy source, Docker, Compose, or inspect Helm rendering | [Deployment](deployment.md) |
| Call `/v1` directly | [HTTP API](api.md) |
| Use generated clients | [SDKs](sdks.md) |
| Add or troubleshoot external sources | [Connectors](connectors.md) and [Troubleshooting](troubleshooting.md) |
