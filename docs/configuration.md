# Configuration

MFS has separate configuration concerns for the CLI and the server.

The server configuration is written by:

```bash
uv run mfs-server setup
```

The setup wizard covers the main backend choices:

- embedding provider
- vision model provider
- Milvus or Zilliz Cloud connection
- metadata database
- cache and object storage
- API auth
- connector defaults

By default, the development server can run with local components: ONNX
embeddings, Milvus Lite, SQLite, local cache storage, and an auto-generated
bearer token.

Common environment variables:

| Variable | Purpose |
|---|---|
| `MFS_HOME` | Server or client state root. |
| `MFS_API_URL` | CLI target server URL. |
| `MFS_API_TOKEN` | Bearer token used by the CLI and SDKs. |
| `ZILLIZ_URI` / `ZILLIZ_TOKEN` | Managed Milvus backend. |
| `OPENAI_API_KEY` | Optional remote embedding or LLM provider. |
