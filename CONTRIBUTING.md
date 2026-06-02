# Contributing to MFS

Thanks for your interest in contributing. MFS is a multi-package monorepo —
this guide walks you through getting each piece running locally and the
checks that have to pass before a PR can merge.

## Project layout

```
mfs/
├── cli/              Rust CLI (mfs binary). Talks to the server via HTTP.
├── server/python/    Python server (FastAPI). Connectors, ingest, search.
├── server-rs/        Rust hot-path acceleration (PyO3 wheel). Optional.
├── sdks/python/      Python SDK (auto-generated from protocol/openapi.yaml).
├── sdks/typescript/  TypeScript SDK (auto-generated).
├── protocol/         OpenAPI spec — source of truth for HTTP / SDK shape.
├── skills/mfs/       Agent skill + per-connector reference.
├── deployments/      Helm chart + container manifests.
└── evaluation/       Embedding / retrieval benchmarks.
```

## Setup

You'll need:

- **Rust 1.85+** (`rustup default stable`)
- **uv** (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- **Python 3.10+** (uv will pin one for you)
- Optionally **pre-commit** (`uv tool install pre-commit`)

```bash
git clone https://github.com/zilliztech/mfs.git
cd mfs

# install pre-commit hooks (runs ruff format + cargo fmt on staged files)
pre-commit install
```

### Interactive setup helpers

Two wizards for operators who'd rather not hand-edit TOML:

```bash
# Base config (embedding / vlm / milvus / database / cache / auth / connectors).
# Press Enter through to get a self-contained-local server.
uv run mfs-server setup
uv run mfs-server setup --section embedding   # change one section later

# Per-connector wizard — pass the URI, the wizard knows the scheme's schema.
# Writes $MFS_HOME/connectors/<alias>.toml and POSTs /v1/add to a running server.
uv run mfs-server connector add postgres://prod-db
uv run mfs-server connector add slack://my-workspace
uv run mfs-server connector add --help        # lists every supported scheme
```

### Build the CLI

```bash
cd cli
cargo build --release        # ./target/release/mfs
./target/release/mfs --help
```

### Run the server

```bash
cd server/python
uv sync                          # default deps
uv sync --extra all-connectors   # include every connector SDK
uv run mfs-server run            # 127.0.0.1:13619
```

Server state (Milvus Lite, sqlite metadata, cache) lives under `$MFS_HOME`
(default `~/.mfs/server/`).

### Optional Rust acceleration

```bash
cd server-rs
uv run --project ../server/python maturin develop --release
```

The Python `mfs_server.common.accel` module imports `mfs_server_rs`
transparently — if the wheel isn't installed, it falls back to pure
Python implementations with identical behaviour.

## Testing

```bash
# Python tests (most are sync; live-API tests are marked 'live' and skipped by default)
cd server/python
uv run pytest                            # default: skips live tests
uv run pytest -m live -k slack          # explicitly run live tests for one connector

# Rust tests
cd cli
cargo test
```

The `server/python/tests/` directory holds public unit + smoke tests.
Integration / e2e suites that depend on real SaaS credentials live in a
separate internal harness — add a `live` marker (`@pytest.mark.live`) when
contributing a test that talks to a real external API so CI can skip it.

## Lint and format

CI enforces format. Lint rules are deliberately loose right now and will
tighten before `v0.4.0` stable.

```bash
# Python
cd server/python
uv run ruff format src/ tests/           # apply
uv run ruff format --check src/ tests/   # CI mode

# Rust
cargo fmt --manifest-path cli/Cargo.toml --all
cargo fmt --manifest-path server-rs/Cargo.toml --all
cargo fmt --manifest-path cli/Cargo.toml --all -- --check        # CI mode
```

Pre-commit will run these automatically on staged files if you ran
`pre-commit install`.

## Commits and pull requests

Use [Conventional Commits](https://www.conventionalcommits.org/) prefixes
in **PR titles** — release notes are auto-generated from PR titles.

| Prefix | Example |
|---|---|
| `feat:` | `feat: add hubspot probe-and-skip for free tier portals` |
| `fix:` | `fix: incremental sync skips renamed files on cross-fs moves` |
| `docs:` | `docs: clarify CS-mode upload flow in file connector ref` |
| `ci:` | `ci: pin rustfmt to stable channel` |
| `chore:` | `chore: bump pyo3 to 0.23` |
| `refactor:` | `refactor: collapse lines field into locator envelope` |
| `test:` | `test: extend jira e2e to cover enhanced_jql pagination` |
| `perf:` | `perf: parallelise sha1 over staged file batches` |

A `!` suffix (`feat!:`, `fix!:`) marks a breaking change.

### Workflow

1. **Branch** from `main`.
2. **Make focused changes** — one feature or fix per PR. Touch only one
   package when reasonable; cross-package PRs are fine when the change
   requires it (CLI flag + server endpoint together, etc).
3. **Add or update tests**. Connectors get a deep e2e suite under
   `server/python/tests/`.
4. **Run lint locally** (`ruff format --check`, `cargo fmt --check`).
5. **Open a PR** to `main` with a conventional-commit title.
6. **Squash merge** is the default — the PR title becomes the commit
   message on `main`, and release-drafter picks it up.

## Reporting issues

https://github.com/zilliztech/mfs/issues

Useful detail: CLI / server version (`mfs --version`, `mfs-server
--version`), connector URI scheme, redacted error output. For server
errors, the failing job id from `mfs job ls` plus `mfs job logs <id>`
output is the most useful single attachment.

## License

By contributing you agree your contributions are licensed under
[Apache-2.0](LICENSE).
