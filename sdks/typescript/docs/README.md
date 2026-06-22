# Generated TypeScript SDK Reference

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
| Package metadata | `@mfs/sdk` `0.4.2` in [`../package.json`](../package.json). |
| Normal server URL | `http://127.0.0.1:13619` for `mfs-server run` / `mfs-server api`. |
| Generated default URL | `http://127.0.0.1:8765`; use it only when you intentionally start a server there, such as for the smoke harness. |
| Bearer auth | When runtime auth is enabled, every request except `GET /healthz` must include `Authorization: Bearer <token>`. |
| OpenAPI auth text | The OpenAPI spec declares bearer auth; stale "no authorization required" text in generated method pages is scaffolding. |
| Examples | Placeholder objects are schema examples, not runnable MFS workflows. Prefer the examples in [`docs/sdks.md`](../../../docs/sdks.md). |

## TypeScript Setup Pattern

```ts
import { Configuration, ServerApi } from "@mfs/sdk";

const basePath = process.env.MFS_URL ?? "http://127.0.0.1:13619";
const token = process.env.MFS_TOKEN;

const configuration = new Configuration({
  basePath,
  headers: token ? { Authorization: `Bearer ${token}` } : undefined,
});

async function main() {
  const server = new ServerApi(configuration);
  const info = await server.getServerInfo();
  console.log(info.version);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
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
