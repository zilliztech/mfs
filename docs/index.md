# MFS

MFS is a multi-source, file-like search layer for agents and developers.
It exposes codebases, local folders, object stores, databases, SaaS tools,
and knowledge bases through a familiar command surface: search, grep, ls,
tree, cat, and status.

The v0.4 line is a client/server system:

- a Rust CLI named `mfs`
- a Python FastAPI server named `mfs-server`
- an OpenAPI protocol under `/v1`
- generated Python and TypeScript SDKs
- optional Rust acceleration for server hot paths

The current beta path is to install the published CLI and run the Python server
from source or from a locally built Docker/Compose server. The server owns
connectors, indexing, retrieval, metadata, cache, and Milvus/Zilliz integration;
clients call the `/v1` control plane.

## Choose your entry point

| Role or task | Start here | Use it for |
|---|---|---|
| New user | [Quickstart](getting-started.md) | Install `mfs`, run `mfs-server`, index a small folder, and verify search plus browse. |
| Daily search or browse user | [Search and Browse](search-and-browse.md) | Follow the `search -> locate -> browse/read` loop with `search`, `grep`, `ls`, `tree`, `cat`, `head`, `tail`, and `export`. |
| Source or connector owner | [Connectors](connectors.md) | Choose a connector scheme, prepare TOML, probe, add, update, inspect, or remove a source. |
| Integration developer | [HTTP API](api.md) and [SDKs](sdks.md) | Call `/v1` directly or use generated Python and TypeScript clients from `protocol/openapi.yaml`. |
| Deployment or operations owner | [Deployment](deployment.md), [Configuration](configuration.md), and [Troubleshooting](troubleshooting.md) | Pick a source, Docker, Compose, or rendered Helm shape; persist state; debug endpoint, auth, jobs, and indexing. |
| Architecture reader | [Architecture](architecture.md), [CLI Reference](cli.md), and [Server](server.md) | Understand how the Rust CLI, generated clients, FastAPI server, connector engine, stores, cache, workers, and OpenAPI protocol fit together. |

## Current v0.4 shape

| Area | Current documentation baseline |
|---|---|
| CLI | `mfs` is the Rust binary and thin HTTP client over `/v1`. |
| Server | `mfs-server` is the Python FastAPI server. For the beta, run it from repository source or a locally built container. |
| Local defaults | Same-host runs use `127.0.0.1:13619`, `$MFS_HOME` defaults to `~/.mfs`, and local first-run backends are ONNX embeddings, Milvus Lite, SQLite, local artifact cache, and an auto-generated bearer token. |
| API and SDKs | `protocol/openapi.yaml` is the API source of truth; SDKs are generated under `sdks/python/` and `sdks/typescript/`. |
| Deployment | Source, Docker, and Compose all-in-one servers are the runnable v0.4 shapes. The Helm api/worker chart is rendered as the post-v0.4 scalable direction. |
