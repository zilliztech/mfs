# mfs — CLI for Multi-source File-like Search

`mfs` is the CLI client for **MFS**: a unified file-like search interface
over codebases, docs, databases and SaaS workspaces. It exposes everything
through familiar shell verbs (`ls`, `cat`, `tree`, `head`, `tail`, `grep`)
plus `mfs search` for hybrid semantic + literal retrieval.

This crate is the binary. The matching server (Python, in the same monorepo)
runs the connectors, ingest pipeline, embedding and retrieval. See the main
project README for the server setup:

**https://github.com/zilliztech/mfs**

## Install

One-line installer (Linux / macOS, x86_64 / arm64):

```bash
curl --proto '=https' --tlsv1.2 -LsSf \
  https://github.com/zilliztech/mfs/releases/latest/download/mfs-cli-installer.sh | sh
```

Or:

```bash
cargo install mfs-cli
```

## Quickstart

Assumes the server is running on `127.0.0.1:13619` (the CLI's default
endpoint). See the main repo for how to start it.

```bash
mfs status                              # server up? connectors registered?
mfs add ./my-repo                       # queue a local directory for indexing
mfs job show JOB_ID                     # wait until status is succeeded

mfs search "rate limit handler" ./my-repo --top-k 5
mfs cat ./my-repo/src/throttle.go --range 42:78
```

`mfs add` returns a queued job id immediately:

```bash
mfs add ./my-repo
mfs job show JOB_ID
```

For the full docs, use:

| Guide | Use it for |
|---|---|
| [Quickstart](../docs/getting-started.md) | First local run and upload-mode choices. |
| [CLI Reference](../docs/cli.md) | Current commands, flags, jobs, connectors, profiles, and JSON output. |
| [Search and Browse](../docs/search-and-browse.md) | Search, locate, and reopen exact evidence. |
| [Connectors](../docs/connectors.md) | Connector catalog, TOML config, credentials, and lifecycle. |
| [Configuration](../docs/configuration.md) | Endpoint, token, profile, and server config precedence. |
| [Deployment](../docs/deployment.md) | Source, Docker, Compose, and beta deployment boundaries. |
| [Troubleshooting](../docs/troubleshooting.md) | Endpoint, auth, upload, indexing, and browse recovery. |

## Pointing at a non-default server

```bash
export MFS_API_URL=http://my-server:13619
mfs status
```

## Status

This is the `v0.4.x` line. The HTTP API may still shift between minor releases.
Track the main repo for changes:

https://github.com/zilliztech/mfs/releases

## License

Apache-2.0.
