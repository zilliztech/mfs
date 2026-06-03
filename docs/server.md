# Server

The Python server is the heavy side of MFS. It exposes the HTTP API and owns
the source registry, connector execution, ingest pipeline, retrieval, metadata,
cache, and Milvus integration.

Run it locally:

```bash
cd server/python
uv sync --extra all-connectors
uv run mfs-server setup
uv run mfs-server run
```

Important areas:

| Path | Role |
|---|---|
| `server/python/src/mfs_server/api/` | HTTP API models and app wiring. |
| `server/python/src/mfs_server/connectors/` | Connector plugins. |
| `server/python/src/mfs_server/storage/` | Milvus, metadata, and cache backends. |
| `server/python/src/mfs_server/server/` | CLI entrypoints and setup wizards. |

Optional Rust acceleration lives in `server-rs/` and is imported by the server
when installed.
