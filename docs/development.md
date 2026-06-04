# Development

Use this page as a compact contributor runbook. It points to the package that
owns each surface, the local setup command to start with, and the checks to run
before you update docs, server code, CLI code, protocol files, generated SDKs,
or the optional Rust acceleration module.

For product usage, start with [Quickstart](getting-started.md). For server
runtime behavior, use [Server](server.md). For exact `/v1` shapes, use
[HTTP API](api.md). For generated clients, use [SDKs](sdks.md).

## Package Boundaries

| Area | Owns | Primary files |
|---|---|---|
| Docs site | Public MkDocs pages, navigation, theme config, and docs-only dependencies. | `docs/`, `mkdocs.yml`, root `pyproject.toml` |
| Rust CLI | The `mfs` binary, endpoint/profile/token resolution, upload packaging, terminal output, and HTTP calls to the server. | `cli/`, `cli/Cargo.toml`, `cli/src/main.rs` |
| Python server | FastAPI `/v1`, connector execution, ingest jobs, upload staging, object processing, search, browse/read, metadata, caches, and Milvus integration. | `server/python/`, `server/python/pyproject.toml` |
| Rust server acceleration | Optional PyO3 extension for server hot paths. The Python server falls back when the wheel is not installed. | `server-rs/`, `server-rs/Cargo.toml`, `server-rs/pyproject.toml` |
| Protocol | Source OpenAPI contract for endpoint paths, operation IDs, tags, and schemas. | `protocol/openapi.yaml` |
| Generated SDKs | Checked-in Python and TypeScript clients generated from the OpenAPI contract. | `sdks/python/`, `sdks/typescript/`, `sdks/generate.sh` |
| SDK smoke harness | Live-server checks for generated clients. These are test harnesses, not shipped SDK artifacts. | `sdks/smoke/` |
| Deployment assets | Docker, Compose, and Helm-rendered runtime shapes. | `deployments/` |

## Local Environment

From the repository root, docs use the root `pyproject.toml` docs dependency
group:

```bash
uv run --group docs mkdocs build --strict
```

The Python server package requires Python 3.10 or newer. Use the server package
directory for server dependencies and checks:

```bash
cd server/python
uv sync
uv sync --extra all-connectors
uv sync --extra dev
uv run mfs-server run
```

`all-connectors` installs the optional connector SDKs. The `dev` extra includes
server test and formatting tools such as `pytest`, `pytest-asyncio`, and
`ruff`.

Build and test the Rust CLI from `cli/`:

```bash
cd cli
cargo build --release
cargo test
cargo fmt --all -- --check
```

Install the optional Rust acceleration wheel from `server-rs/` when you need to
exercise the server with native hot paths enabled:

```bash
cd server-rs
uv run --project ../server/python maturin develop --release
```

!!! warning "Verify old snippets before reusing them"
    Broad setup prose can lag the detailed reference pages. Before copying job
    commands or state paths, check [CLI Reference](cli.md),
    [Troubleshooting](troubleshooting.md), and [Configuration](configuration.md).
    Current job commands use `mfs job list`, `mfs job show JOB_ID`, and
    `mfs job cancel JOB_ID`. `MFS_HOME` defaults to `~/.mfs`, and local server
    files such as `server.token`, `metadata.db`, `transformation_cache.db`,
    `cache/`, and `milvus.db` live under that root unless `MFS_HOME` is set.

## Checks by Change Type

| Change | Run | Notes |
|---|---|---|
| Docs page, nav, or docs dependency | `uv run --group docs mkdocs build --strict` | Matches the docs build workflow. Run from the repository root. |
| Python server formatting | `cd server/python && uv sync --extra dev && uv run ruff format --check src/ tests/` | Matches the Python formatting job. Use `uv run ruff format src/ tests/` to apply formatting. |
| Python server tests | `cd server/python && uv run pytest` | Default test path is `tests/`. Live connector tests are marked separately, for example `uv run pytest -m live -k slack`. |
| Rust CLI formatting | `cd cli && cargo fmt --all -- --check` | Matches the CLI rustfmt job. |
| Rust CLI behavior | `cd cli && cargo test` | Use with `cargo build --release` when you need to verify the local `mfs` binary. |
| Rust server acceleration formatting | `cd server-rs && cargo fmt --all -- --check` | Matches the server-rs rustfmt job. |
| Rust server acceleration behavior | `cd server-rs && cargo test` plus `uv run --project ../server/python maturin develop --release` | `maturin develop` installs the PyO3 module into the server environment for integration checks. |
| OpenAPI contract | Regenerate SDKs, then review generated API classes and docs | `protocol/openapi.yaml` is the contract source. See [OpenAPI to SDKs](#openapi-to-sdks). |
| Generated SDKs | Run generator and inspect `sdks/python/` and `sdks/typescript/` | Generated README files and package internals can contain scaffold defaults. Confirm before documenting method names, auth text, versions, or base URLs. |
| SDK smoke harness | Run the smoke commands against a prepared live server | The smoke README uses `127.0.0.1:8765`; that is a harness target, not the default `mfs-server run` address. |
| Deployment docs or assets | Run the docs build; for asset changes, render or lint the asset you changed | The docs build is the public-docs gate. Deployment topology details live in [Deployment](deployment.md). |

The checked workflows currently enforce:

| Workflow | Check |
|---|---|
| `.github/workflows/docs.yml` | `uv run --group docs mkdocs build --strict` |
| `.github/workflows/lint.yml` | `uv run ruff format --check src/ tests/` from `server/python` |
| `.github/workflows/lint.yml` | `cargo fmt --all -- --check` from `cli` |
| `.github/workflows/lint.yml` | `cargo fmt --all -- --check` from `server-rs` |

## OpenAPI to SDKs

```text
protocol/openapi.yaml
        |
        v
sdks/generate.sh
        |
        +--> sdks/python/      package: mfs_sdk
        +--> sdks/typescript/  package: @mfs/sdk
        |
        v
sdks/smoke/ live-server harness
```

Regenerate clients after changing `protocol/openapi.yaml`. The generator script
requires Java 11 or newer and `openapi-generator-cli`.

```bash
npm install -g @openapitools/openapi-generator-cli
cd sdks
./generate.sh
```

Then inspect the generated SDKs before documenting a method name or field:

| Inspect | Why |
|---|---|
| `sdks/python/mfs_sdk/api/` and `sdks/typescript/src/apis/` | Confirms which operation groups became generated client classes. |
| `sdks/python/pyproject.toml` and `sdks/typescript/package.json` | Confirms checked-in package metadata. |
| `sdks/python/README.md` and `sdks/typescript/README.md` | Generated docs can contain scaffold authorization, version, or default-host text. Treat them as generated output to verify, not as runtime authority. |
| [HTTP API](api.md) and [SDKs](sdks.md) | Keep public integration docs aligned with the regenerated sources. |

The smoke harnesses run against a live server after a small fixture has been
added. They cover search-to-envelope, `ls`, `cat`, `status`, and error mapping.

```bash
cd sdks/smoke
cd python && uv pip install -e ../../python && python smoke_test.py
```

```bash
cd sdks/smoke
(cd ../typescript && npm i && npm run build) && node typescript/smoke_test.cjs
```

!!! note "Smoke harness address"
    The smoke README and smoke scripts target `127.0.0.1:8765`. The default
    `mfs-server run` and `mfs-server api` bind address is `127.0.0.1:13619`.
    Set up the live test server deliberately before running the harness.

## What To Open Next

| Need | Page |
|---|---|
| Runtime module map and server entrypoints | [Server](server.md) |
| Exact endpoint paths, request fields, and response schemas | [HTTP API](api.md) |
| Generated client coverage, examples, and SDK caveats | [SDKs](sdks.md) |
| Config lookup, `MFS_HOME`, auth modes, and backend defaults | [Configuration](configuration.md) |
| Source, Docker, Compose, and rendered Helm runtime shapes | [Deployment](deployment.md) |
| Endpoint, auth, upload, indexing, search, and browse failures | [Troubleshooting](troubleshooting.md) |
