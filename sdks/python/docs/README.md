# Generated Python SDK Reference

> **Read this before using the generated pages**
>
> The files in this directory are generated API/model reference pages. They are
> useful for method names and generated model fields, but they are not the
> source of truth for current MFS runtime behavior. Use
> [`docs/sdks.md`](../../../docs/sdks.md) for curated SDK setup and
> [`docs/api.md`](../../../docs/api.md) for HTTP runtime behavior.

## Runtime Values

| Item | Current guidance |
|---|---|
| Package metadata | `mfs_sdk` `0.4.2` in [`../pyproject.toml`](../pyproject.toml). |
| Normal server URL | `http://127.0.0.1:13619` for `mfs-server run` / `mfs-server api`. |
| Generated default URL | `http://127.0.0.1:8765`; use it only when you intentionally start a server there, such as for the smoke harness. |
| Bearer auth | When runtime auth is enabled, every request except `GET /healthz` must include `Authorization: Bearer <token>`. |
| OpenAPI auth text | The OpenAPI spec declares bearer auth; stale "no authorization required" text in generated method pages is scaffolding. |
| Examples | Placeholder JSON blocks are schema examples, not runnable MFS workflows. Prefer the examples in [`docs/sdks.md`](../../../docs/sdks.md). |

## Python Setup Pattern

```python
import os

import mfs_sdk


configuration = mfs_sdk.Configuration(
    host=os.getenv("MFS_URL", "http://127.0.0.1:13619")
)

with mfs_sdk.ApiClient(configuration) as api_client:
    token = os.getenv("MFS_TOKEN")
    if token:
        api_client.set_default_header("Authorization", f"Bearer {token}")

    server = mfs_sdk.ServerApi(api_client)
    print(server.get_server_info().version)
```

## Generated Pages

| Need | Page |
|---|---|
| Browse/read generated methods | [`BrowseApi.md`](BrowseApi.md) |
| Ingest/job generated methods | [`IngestApi.md`](IngestApi.md) |
| Search/grep generated methods | [`RetrievalApi.md`](RetrievalApi.md) |
| Server info/status generated methods | [`ServerApi.md`](ServerApi.md) |
| Search result model | [`ResultEnvelope.md`](ResultEnvelope.md) |
| Add request model | [`AddRequest.md`](AddRequest.md) |

Generated client coverage is smaller than the full OpenAPI contract. Use the
curated [SDKs](../../../docs/sdks.md#api-mapping) page for the checked method
mapping and [HTTP API](../../../docs/api.md#workflow-matrix) for the complete
endpoint list.
