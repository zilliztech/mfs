# Auth and Secrets

Use this page when you need to identify which MFS process needs which token,
where that value is read from, and what to check first after a `401` or missing
credential error. For the full setting tables, see
[Configuration](configuration.md). For topology-specific commands, see
[Deployment](deployment.md).

## Process Boundaries

| Actor | Needs | Reads from | Notes |
|---|---|---|---|
| `mfs-server run` or `mfs-server api` | API bearer token for `/v1` auth | Resolved `auth_token`, server-side `MFS_API_TOKEN`, or `$MFS_HOME/server.token` | If no token is configured, the entrypoint creates or reuses `server.token`. |
| `mfs` CLI | Bearer token for the target `/v1` server | CLI-side `MFS_API_TOKEN`, active profile token, or local `$MFS_HOME/server.token` | The local file fallback only helps when the CLI can read the same `MFS_HOME` as the server. |
| Direct HTTP or SDK client | Bearer token for the target `/v1` server | The caller's own secret source | Send `Authorization: Bearer <token>` when server auth is enabled. |
| Connector plugin | Connector credentials such as database DSNs, SaaS tokens, or PEM keys | Top-level connector config values resolved from `env:VAR` or `file:/abs/path` by the server/plugin process | The client shell does not resolve connector credential references. |
| Docker or Compose server container | Server API token and provider/backend secrets | Container environment and `/data/server.token` | Compose sets `MFS_HOME=/data`; persist `/data` to keep generated tokens and state. |
| Helm API and worker pods | Shared API token plus backend/provider secrets | The configured Kubernetes Secret and chart values | The chart helper injects the same environment block into API and worker pods. |

## Server API Auth

`/v1` endpoints are protected only when the resolved server config has
`auth_token` set. `mfs-server run` and `mfs-server api` make auth the default by
creating or reusing a token file when no token is configured.

| Server-side mode | How to configure it | Runtime behavior |
|---|---|---|
| Auto token | Omit `auth_token` in `server.toml`, or choose auto in `mfs-server setup --section auth`. | `mfs-server setup`, `run`, or `api` creates or reuses `$MFS_HOME/server.token`. |
| Known token | Set `auth_token = "..."` in TOML, or set non-empty `MFS_API_TOKEN` in the server environment. | The server requires that value as `Authorization: Bearer <token>` on `/v1`. |
| Explicitly open | Set the resolved `auth_token` value to `-`. | `mfs-server run` and `api` convert it to no auth. Use only for an intentionally open trusted or isolated network. |

`MFS_API_TOKEN` has two meanings:

| Process | Meaning |
|---|---|
| Server process | Runtime override for `auth_token`. |
| CLI process | Highest-priority bearer token source sent to the server. |

Set the same known value in the server environment and in every remote client
environment that should call `/v1`.

!!! note "Health checks are not auth checks"
    `GET /healthz` is outside the OpenAPI `/v1` control plane and the FastAPI
    auth middleware exempts it. It returns only `{"status":"ok"}` so liveness
    and readiness probes can work without API credentials. Use
    `/v1/server/info` or `/v1/status` with a bearer token to test API auth.

## CLI and API Token Sources

The CLI chooses its endpoint separately from its bearer token. Endpoint
precedence is documented in [CLI Reference](cli.md#global-behavior) and
[Configuration](configuration.md#cli-endpoint-and-token-resolution). Token
precedence is:

| Priority | CLI bearer token source | Behavior |
|---:|---|---|
| 1 | Non-empty `MFS_API_TOKEN` | Wins over profile tokens and local token files. |
| 2 | Active profile token in `$MFS_HOME/client.toml` | May be a literal token or `env:VAR`. If `env:VAR` resolves empty, the CLI sends no bearer token from that profile. |
| 3 | `$MFS_HOME/server.token` | Same-host fallback for local auto-token servers. |

For direct HTTP and SDK callers, there is no CLI fallback. Send the header
explicitly:

```bash
export MFS_URL=http://127.0.0.1:13619
export MFS_API_TOKEN="replace-with-your-token"
curl -sS -H "Authorization: Bearer $MFS_API_TOKEN" \
  "$MFS_URL/v1/server/info"
```

## Connector Credentials

Connector TOML is loaded by the CLI and sent as the API `config` object. The
server resolves supported credential references when it builds the connector
plugin.

| Form | Resolved by | Behavior | Use for |
|---|---|---|---|
| `env:VAR` | Server/plugin process environment | Reads `VAR` when the connector is constructed; missing variables fail fast. | Tokens, DSNs, and short secrets injected into the server process. |
| `file:/abs/path` | Server/plugin process filesystem | Reads the file contents and strips trailing whitespace. The public connector reference requires an absolute path. | Mounted secrets, PEM keys, and multi-line credentials. |
| Plaintext string | No indirection | Passed as the literal value and may be stored in connector TOML or metadata after redaction rules apply. | Short-lived demos only when you accept the storage risk. |

Use `credential_ref` when a connector expects the resolved value through the
plugin `credential` fallback. Snowflake's private key config is one example:

```toml
credential_ref = "file:/etc/mfs/snowflake/rsa_key.p8"
```

When MFS runs in Docker or Kubernetes, `env:VAR` and `file:/abs/path` are
resolved inside the container or pod. Exporting a variable only in the shell
that runs `mfs add` is not enough unless that shell is also starting the server.
For connector-specific fields and pitfalls, see [Connectors](connectors.md) and
the per-connector pages.

## Deployment Injection

| Topology | API token path | Other secret paths | Notes |
|---|---|---|---|
| Source server | `MFS_API_TOKEN` in the server environment, or `$MFS_HOME/server.token` in auto mode | Server environment variables such as `MILVUS_TOKEN`, `ZILLIZ_TOKEN`, `ZILLIZ_API_KEY`, and provider SDK env vars | `MFS_HOME` defaults to `~/.mfs`. |
| Docker all-in-one | `docker run -e MFS_API_TOKEN=...`, or `/data/server.token` when omitted | `docker run -e ...` for server-read env vars | Mount `/data` so generated tokens and state survive container removal. |
| Compose all-in-one | `MFS_API_TOKEN: ${MFS_API_TOKEN:-}` and `MFS_HOME=/data` in `deployments/compose/docker-compose.yml` | Compose passes `OPENAI_API_KEY` and the `MILVUS_*` / `ZILLIZ_*` endpoint vars | If `MFS_API_TOKEN` is empty, read `/data/server.token` from the container. |
| Helm-rendered API/worker | `MFS_API_TOKEN` from existing Secret key `api-token` | Secret keys `zilliz-token` and `openai-api-key` | Every API replica must share the same `api-token`. API probes use unauthenticated `/healthz`. |

## First Failure Recovery

| Symptom | First commands | What to fix |
|---|---|---|
| `mfs status` returns `401 [unauthorized]` | `mfs config show` | Set the intended CLI-side `MFS_API_TOKEN`, fix the active profile token, or read the same-host `server.token`. |
| `/healthz` works but `/v1/server/info` returns `401` | `curl http://127.0.0.1:13619/healthz` then `curl -H "Authorization: Bearer $MFS_API_TOKEN" http://127.0.0.1:13619/v1/server/info` | Health proves process liveness only; send the API bearer token for `/v1`. |
| Local source server uses auto auth | `export MFS_API_TOKEN="$(cat ${MFS_HOME:-$HOME/.mfs}/server.token)"` | Use the generated local server token for direct HTTP or remote-style CLI testing. |
| Host CLI talks to Docker | `export MFS_API_TOKEN="$(docker exec mfs-server cat /data/server.token)"` | Match the host client token to the container's server token, or restart with a known `MFS_API_TOKEN`. |
| Host CLI talks to Compose | `docker compose -f deployments/compose/docker-compose.yml exec -T mfs-server cat /data/server.token` | Export that value as host `MFS_API_TOKEN`, or start Compose with a known token. |
| Active profile uses `env:VAR` and auth is missing | `mfs config show` and `test -n "$VAR" && echo set || echo missing` | Export `VAR` in the CLI environment, update the profile token, or set CLI-side `MFS_API_TOKEN`. |
| Connector reports missing environment variable | `mfs connector probe TARGET --config FILE` | Set the variable in the server process environment or container/pod spec, then retry probe/add. |
| Connector cannot read `file:/abs/path` | Check the path from the server host, container, or pod | Mount the file where the server process can read it, use an absolute `file:` path, and retry probe/add. |
| Need canonical error meaning | Open [Error Codes](errors.md) | `unauthorized` means server auth is enabled and the bearer token is missing or wrong. |

For deeper runbooks, use [Troubleshooting](troubleshooting.md#endpoint-and-auth)
for endpoint/auth failures and [Troubleshooting](troubleshooting.md#connector-failures)
for connector failures. For the server-side implementation boundary, see
[Server](server.md#auth-and-config-boundary).
