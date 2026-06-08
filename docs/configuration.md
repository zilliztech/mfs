# Configuration

MFS has two configuration surfaces:

- the Python server configures storage, embeddings, auth, indexing, and cache
  backends;
- the Rust CLI configures the target endpoint, client identity, profiles, and
  bearer token source.

Use this page when you need to answer "which value wins?" before changing a
deployment. For first-run commands, start with [Quickstart](getting-started.md).
For a compact process-to-token map and first auth recovery commands, see
[Auth and Secrets](auth-and-secrets.md).
For container and Helm topology details, see [Deployment](deployment.md).
For embedding, summary, VLM, and conversion provider choices, see
[Providers and Processing](providers.md).
For backup, restore, and safe reset boundaries, see
[Storage and Backup](storage-and-backup.md).

```text
mfs CLI
  endpoint: MFS_API_URL -> active profile -> http://127.0.0.1:13619
  token:    MFS_API_TOKEN -> active profile token -> $MFS_HOME/server.token
      |
      v
mfs-server /v1
  config: --config -> MFS_SERVER_CONFIG -> ./server.toml
          -> $MFS_HOME/server.toml -> ~/.mfs/server.toml
          -> /etc/mfs/server.toml -> built-in defaults
```

## Server Config Lookup

`mfs-server run`, `mfs-server api`, `mfs-server worker`, and `mfs-server reload`
load server settings through `load_server_config`. The first existing file in
this chain wins:

| Priority | Source | Notes |
|---|---|---|
| 1 | `--config PATH` | Supported by `mfs-server run`, `api`, `worker`, and `reload`. |
| 2 | `MFS_SERVER_CONFIG` | Server-side environment override for the config file path. |
| 3 | `./server.toml` | Relative to the current working directory of the server process. |
| 4 | `$MFS_HOME/server.toml` | Checked when `MFS_HOME` is set. This is the wizard's default write target. |
| 5 | `~/.mfs/server.toml` | Default user-level server config when `MFS_HOME` is not set. |
| 6 | `/etc/mfs/server.toml` | System-level fallback. |
| 7 | built-in defaults | Local ONNX, Milvus Lite, SQLite, local artifact cache, auth token bootstrap. |

`MFS_HOME` defaults to `~/.mfs`. The server creates that directory when it
resolves config defaults.

!!! note "Environment overrides still apply"
    After the server loads TOML and resolves default paths, selected environment
    variables can override runtime fields. See [Environment Overrides](#environment-overrides).

## Files Under `MFS_HOME`

The same root is used by the local server and local CLI defaults. In a source
run it defaults to `~/.mfs`; the Docker image and Compose setup use `/data`.

| Path | Owner | Created by | Purpose |
|---|---|---|---|
| `$MFS_HOME/server.toml` | Server | `mfs-server setup` by default | Server backend, auth, cache, embedding, and search settings. |
| `$MFS_HOME/server.token` | Server and local CLI | `mfs-server setup` in auto auth mode, or `mfs-server run` / `api` when no token exists | Bearer token for local CLI fallback and direct HTTP clients that can read it. |
| `$MFS_HOME/client.toml` | CLI | `mfs profile add`, `mfs profile use`, or first `client_id` generation | Active profile, profile endpoints, profile tokens, and stable client id. |
| `$MFS_HOME/metadata.db` | Server | Server defaults when SQLite is used | Connector registry, object metadata, jobs, and related metadata. |
| `$MFS_HOME/transformation_cache.db` | Server | Server defaults when SQLite transformation cache is used | Transformation-cache lookup table. |
| `$MFS_HOME/cache` | Server | Server defaults when local artifact cache is used | Derived artifact blobs such as converted document text or image descriptions. |
| `$MFS_HOME/milvus.db` | Server | Server defaults when no remote Milvus URI is configured | Milvus Lite vector database. |
| `$MFS_HOME/onnx-cache/` | Server embedding provider | First local ONNX server/worker startup, or setup dimension probe | Cached ONNX model files for `gpahal/bge-m3-onnx-int8`. |

!!! warning "Persist this directory"
    In containers, mount `/data` to a durable volume. Otherwise the server
    loses SQLite state, Milvus Lite data, caches, and the generated bearer
    token when the container is removed.

## Setup Wizard Sections

Run the full server wizard:

```bash
uv run mfs-server setup
```

Run a single section later:

```bash
uv run mfs-server setup --section embedding
uv run mfs-server setup --section auth
```

Write and run a specific config file:

```bash
uv run mfs-server setup --config /tmp/mfs-server.toml
uv run mfs-server run --config /tmp/mfs-server.toml
```

The wizard walks these sections in order:

| Section | TOML fields | Default behavior | When to change it |
|---|---|---|---|
| `embedding` | `[embedding] provider`, `model`, `dim` | Local ONNX provider, `gpahal/bge-m3-onnx-int8`, 1024 dimensions. | Use hosted or local alternatives when you have the required extra dependencies and credentials. |
| `description` | `[summary] enabled`, `include_image_description`, `provider`, `model`; `[description] provider`, `model` | Off by default. Image objects can be listed without generating image descriptions. | Enable when you need image descriptions in the searchable index and accept the provider cost. |
| `milvus` | `[milvus] uri`, `token` | Milvus Lite under `$MFS_HOME/milvus.db`. | Set an HTTP(S) URI for Milvus or Zilliz Cloud. |
| `database` | `[database] backend`, `dsn` | SQLite. The same backend feeds metadata and the transformation-cache lookup table. | Use Postgres when multiple server processes need shared relational state. |
| `cache` | `[artifact_cache] backend`, `root`, S3-compatible fields | Local filesystem under `$MFS_HOME/cache`. | Use S3, MinIO, R2, or GCS-compatible storage for shared artifact blobs. |
| `auth` | top-level `auth_token` | Auto mode omits `auth_token` in TOML and creates or reuses `server.token`. | Provide a known token, or set `-` only for an intentionally open trusted network. |

??? note "Advanced and legacy TOML blocks"
    `metadata.backend` / `metadata.dsn` and `transformation_cache.backend` /
    `transformation_cache.dsn` can override the unified `[database]` block for
    power users. Existing legacy TOML that used older `[metadata]`,
    `[transformation_cache]`, or `[object_store]` blocks is migrated in memory
    when the server loads config.

## Default Local Backends

With no TOML file and no runtime overrides, the server resolves to local
backends:

| Concern | Default |
|---|---|
| Home | `~/.mfs`, unless `MFS_HOME` is set. |
| API bind address | `127.0.0.1:13619` for `mfs-server run` and `api`. |
| Namespace | `default`. |
| Embedding | ONNX provider, `gpahal/bge-m3-onnx-int8`, 1024 dimensions, batch size 100. |
| VLM and summaries | Directory summaries and image descriptions are off by default. |
| Vector database | Milvus Lite at `$MFS_HOME/milvus.db`. |
| Database | SQLite for metadata and the transformation-cache lookup table. |
| Metadata SQLite path | `$MFS_HOME/metadata.db`. |
| Transformation cache SQLite path | `$MFS_HOME/transformation_cache.db`. |
| Artifact cache | Local filesystem at `$MFS_HOME/cache`, `max_size_gb = 10.0`, `eviction = "lru"`. |
| Job runner | `[server] in_process_jobrunner = true`, `[chunks_producer] concurrency = 8`, `[object_task] max_retries = 3`. |
| Chunking | `[chunking] chunk_size = 2048`, `default_chunk_max = 1000000`. |
| Search | `over_fetch_ratio = 3`, `max_partitions_per_query = 32`. |

## Auth Modes

`/v1` endpoints require `Authorization: Bearer <token>` when `auth_token` is
configured. `GET /healthz` is exempt so health probes work without secrets.

| Server-side mode | How to configure | What happens |
|---|---|---|
| Auto token | Omit `auth_token` in `server.toml`, or choose auto in the wizard. | `mfs-server setup` or `mfs-server run` / `api` creates or reuses `$MFS_HOME/server.token`. |
| Known token | Set `auth_token = "..."` in TOML, or set `MFS_API_TOKEN` in the server environment. | The server expects that token on `/v1` requests. |
| Explicitly open | Set `auth_token = "-"` in TOML. | `mfs-server run` / `api` converts it to no auth. Use only for trusted or isolated networks. |

!!! warning "Token names have client and server meanings"
    `MFS_API_TOKEN` is both a server-side runtime override for `auth_token` and
    the CLI's highest-priority bearer token source. For remote or container
    servers, set the same value on the server and on every client that should
    call `/v1`.

## Environment Overrides

These variables are read by the current server or CLI code. Runtime overrides
apply after TOML is loaded and default paths are resolved.

| Variable | Read by | Effect |
|---|---|---|
| `MFS_HOME` | Server and CLI | Sets the root for `server.toml`, `server.token`, `client.toml`, SQLite files, local cache, Milvus Lite, and ONNX cache. |
| `MFS_SERVER_CONFIG` | Server | Adds a config-file lookup path after `--config`. |
| `MFS_API_TOKEN` | Server and CLI | Server: overrides `auth_token`. CLI: highest-priority bearer token source. |
| `MFS_API_URL` | CLI | Highest-priority endpoint source. |
| `MFS_SUMMARY_ENABLED` | Server | Sets `summary.enabled`. Truthy values are `1`, `true`, `yes`, and `on`. |
| `MILVUS_URI` | Server | Primary Milvus or Zilliz Cloud URI override. |
| `MILVUS_TOKEN` | Server | Primary Milvus or Zilliz token override. |
| `ZILLIZ_URI` | Server | Fallback URI when `MILVUS_URI` is unset. |
| `ZILLIZ_TOKEN` | Server | Fallback token when `MILVUS_TOKEN` is unset. |
| `ZILLIZ_API_KEY` | Server | Additional fallback token when `MILVUS_TOKEN` and `ZILLIZ_TOKEN` are unset. |
| `MFS_METADATA_DSN` | Server | Switches the unified database backend to Postgres and applies the DSN to metadata. |
| `MFS_TX_CACHE_DSN` | Server | Optional transformation-cache Postgres DSN. |
| `MFS_TX_CACHE_PG` | Server | Enables Postgres for transformation cache when paired with `MFS_TX_CACHE_DSN` or `MFS_METADATA_DSN`. |
| `MFS_OBJECT_STORE_BUCKET` | Server | Switches artifact cache to S3-compatible storage and sets the bucket. |
| `MFS_OBJECT_STORE_ENDPOINT` | Server | Sets S3-compatible endpoint URL when object-store bucket is configured. |
| `MFS_OBJECT_STORE_REGION` | Server | Sets S3-compatible region when object-store bucket is configured. |
| `MFS_OBJECT_STORE_PREFIX` | Server | Sets S3-compatible prefix when object-store bucket is configured. |
| `MFS_OBJECT_STORE_ACCESS_KEY` | Server | Sets S3-compatible access key when object-store bucket is configured. |
| `MFS_OBJECT_STORE_SECRET_KEY` | Server | Sets S3-compatible secret key when object-store bucket is configured. |
| `OPENAI_API_KEY` | OpenAI provider SDK | Needed only when OpenAI-backed embedding, summary, or VLM settings are selected. The default ONNX path does not require it. |

!!! warning "Do not use `MFS_MILVUS_*` as runtime overrides"
    Some deployment assets mention `MFS_MILVUS_URI` and `MFS_MILVUS_TOKEN`.
    The current server configuration code does not read those names. Use
    `MILVUS_URI` / `MILVUS_TOKEN`, `ZILLIZ_URI` / `ZILLIZ_TOKEN`, or
    `server.toml` until the deployment assets and runtime config are aligned.

## CLI Endpoint and Token Resolution

The CLI stores profiles in `$MFS_HOME/client.toml`.

Endpoint precedence:

| Priority | Source | Notes |
|---|---|---|
| 1 | `MFS_API_URL` | Use for one-off shells, containers, CI, or scripts. |
| 2 | active profile URL in `$MFS_HOME/client.toml` | Managed by `mfs profile add` and `mfs profile use`. |
| 3 | `http://127.0.0.1:13619` | Default local server endpoint. |

Bearer token precedence:

| Priority | Source | Notes |
|---|---|---|
| 1 | non-empty `MFS_API_TOKEN` | Wins over profile tokens and local token files. |
| 2 | active profile token | May be a literal token or `env:VAR`; if `env:VAR` resolves empty, no token is sent from that profile. |
| 3 | `$MFS_HOME/server.token` | Local same-host fallback for the auto-generated server token. |

Create a persistent remote profile:

```bash
mfs profile add prod https://mfs.example.com --token env:MFS_API_TOKEN
mfs profile use prod
mfs config show
```

A generated client config uses TOML tables like this:

```toml
active = "prod"
client_id = "generated-on-first-use"

[profiles.prod]
url = "https://mfs.example.com"
token = "env:MFS_API_TOKEN"
```

`mfs config show` prints the resolved endpoint, active profile, stable
`client_id`, and whether `/v1/server/info` is reachable.

!!! note "Remote paths and upload mode"
    The CLI treats non-loopback endpoints as remote. For local paths, it may
    rewrite browse/search paths to the uploaded connector identity. For
    practical host-versus-container examples, see [Deployment](deployment.md)
    and [Troubleshooting](troubleshooting.md).

## Minimal Examples

=== "Local defaults"

    ```bash
    cd mfs/server/python
    uv sync
    uv run mfs-server setup
    uv run mfs-server run
    ```

    In another terminal:

    ```bash
    mfs status
    mfs config show
    ```

=== "Known token"

    ```bash
    export MFS_API_TOKEN="replace-with-a-shared-token"
    uv run mfs-server run
    ```

    On clients:

    ```bash
    export MFS_API_URL=http://127.0.0.1:13619
    export MFS_API_TOKEN="replace-with-a-shared-token"
    mfs status
    ```

=== "Remote Milvus"

    ```bash
    export MILVUS_URI="$ZILLIZ_URI"
    export MILVUS_TOKEN="$ZILLIZ_TOKEN"
    uv run mfs-server run
    ```

=== "Postgres metadata"

    ```bash
    export MFS_METADATA_DSN="postgresql://user:pass@host:5432/mfs"
    uv run mfs-server run
    ```

=== "S3-compatible artifact cache"

    ```bash
    export MFS_OBJECT_STORE_BUCKET="mfs-artifacts"
    export MFS_OBJECT_STORE_ENDPOINT="https://s3.example.com"
    export MFS_OBJECT_STORE_REGION="us-east-1"
    export MFS_OBJECT_STORE_ACCESS_KEY="..."
    export MFS_OBJECT_STORE_SECRET_KEY="..."
    uv run mfs-server run
    ```

## Related Guides

| Guide | Use it for |
|---|---|
| [Quickstart](getting-started.md) | First local source run and search/read checkpoint. |
| [Auth and Secrets](auth-and-secrets.md) | Process boundaries, token precedence, connector credentials, and first auth recovery commands. |
| [Providers and Processing](providers.md) | Embedding, summary, VLM, converter setup, provider extras, and provider error surfaces. |
| [Deployment](deployment.md) | Source, Docker, Compose, and Helm topology notes. |
| [Storage and Backup](storage-and-backup.md) | State components, backup targets, restore order, and reset boundaries. |
| [Connectors](connectors.md) | Connector catalog, config files, credentials, and lifecycle commands. |
| [CLI Reference](cli.md) | Command flags, profiles, upload mode, jobs, and browse commands. |
| [HTTP API](api.md) | Direct `/v1` requests, bearer auth, response envelopes, and schema fields. |
| [Troubleshooting](troubleshooting.md) | Endpoint, auth, upload, indexing, and search failure recovery. |
