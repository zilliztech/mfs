# @mfs/sdk

A TypeScript SDK client for the MFS HTTP `/v1` API.

> **Current MFS runtime guidance**
>
> This README is generated OpenAPI reference plus a checked-in runtime overlay.
> For curated v0.4 guidance, use [`docs/sdks.md`](../../docs/sdks.md) and
> [`docs/api.md`](../../docs/api.md).
>
> - Package metadata in [`package.json`](package.json) is `@mfs/sdk`
>   `0.4.0`.
> - Normal `mfs-server run` / `mfs-server api` runs default to
>   `http://127.0.0.1:13619`.
> - The generated client default is `http://127.0.0.1:8765`; use that only for
>   generated or smoke-test runs when you intentionally start a server there.
> - `mfs-server run` / `mfs-server api` bootstrap or reuse
>   `$MFS_HOME/server.token` unless `auth_token = "-"` is configured.
> - When auth is enabled, every request except `GET /healthz` must include
>   `Authorization: Bearer <token>`.
> - Generated "no authorization" text comes from the OpenAPI scaffold, not from
>   the runtime auth middleware.

## Current Runtime Setup

Set the base URL and token explicitly before creating API classes:

```bash
export MFS_URL=http://127.0.0.1:13619
export MFS_TOKEN=replace-with-your-token
```

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
  console.log((await server.getServerInfo()).version);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
```

## Usage

This checked-in README does not assert npm package availability. Install or link
the checked-in `sdks/typescript` package through the repository release or
development workflow you are using, then try it out.


```ts
import {
  Configuration,
  BrowseApi,
} from '@mfs/sdk';
import type { CatRequest } from '@mfs/sdk';

async function example() {
  const basePath = process.env.MFS_URL ?? "http://127.0.0.1:13619";
  const token = process.env.MFS_TOKEN;
  const api = new BrowseApi(new Configuration({
    basePath,
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  }));

  const body = {
    // string
    path: process.env.MFS_TARGET ?? "/tmp/mfs_sdk_fixture/auth.md",
    // string (optional), for example "1:120"
    range: undefined,
    // boolean (optional)
    meta: false,
    // string (optional), for example "normal"
    density: undefined,
  } satisfies CatRequest;

  try {
    const data = await api.cat(body);
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the example
example().catch(console.error);
```


## Documentation

### API Endpoints

All URIs are relative to the configured client host. The generated default host
is *http://127.0.0.1:8765*; normal `mfs-server run` / `mfs-server api` runs
default to *http://127.0.0.1:13619*.

| Class | Method | HTTP request | Description
| ----- | ------ | ------------ | -------------
*BrowseApi* | [**cat**](docs/BrowseApi.md#cat) | **GET** /v1/cat | Cat
*BrowseApi* | [**ls**](docs/BrowseApi.md#ls) | **GET** /v1/ls | Ls
*IngestApi* | [**addSource**](docs/IngestApi.md#addsource) | **POST** /v1/add | Add
*IngestApi* | [**cancelJob**](docs/IngestApi.md#canceljob) | **POST** /v1/jobs/{job_id}/cancel | Cancel Job
*IngestApi* | [**getJob**](docs/IngestApi.md#getjob) | **GET** /v1/jobs/{job_id} | Job
*IngestApi* | [**uploadSource**](docs/IngestApi.md#uploadsource) | **POST** /v1/upload | Upload
*RetrievalApi* | [**grep**](docs/RetrievalApi.md#grep) | **GET** /v1/grep | Grep
*RetrievalApi* | [**search**](docs/RetrievalApi.md#search) | **GET** /v1/search | Search
*ServerApi* | [**getServerInfo**](docs/ServerApi.md#getserverinfo) | **GET** /v1/server/info | Server Info
*ServerApi* | [**status**](docs/ServerApi.md#status) | **GET** /v1/status | Status


### Models

Read the [generated reference notes](docs/README.md) before using generated
model examples. Placeholder objects in model pages are schema scaffolding, not
runnable runtime examples.

- [AddRequest](docs/AddRequest.md)
- [AddResponse](docs/AddResponse.md)
- [CancelResponse](docs/CancelResponse.md)
- [CatResponse](docs/CatResponse.md)
- [ConnectorRow](docs/ConnectorRow.md)
- [GrepMatchModel](docs/GrepMatchModel.md)
- [GrepResponse](docs/GrepResponse.md)
- [HTTPValidationError](docs/HTTPValidationError.md)
- [JobResponse](docs/JobResponse.md)
- [LocationInner](docs/LocationInner.md)
- [LsEntry](docs/LsEntry.md)
- [LsResponse](docs/LsResponse.md)
- [ResultEnvelope](docs/ResultEnvelope.md)
- [SearchResponse](docs/SearchResponse.md)
- [ServerInfo](docs/ServerInfo.md)
- [StatusResponse](docs/StatusResponse.md)
- [ValidationError](docs/ValidationError.md)

### Authorization

The OpenAPI spec declares bearer auth. If this checked-in generated client does
not attach authorization automatically, set the header explicitly:

```ts
new Configuration({
  headers: { Authorization: `Bearer ${token}` },
});
```

When the server is configured with `auth_token`, every request except
`GET /healthz` must include that header. See [`docs/api.md`](../../docs/api.md)
and [`docs/sdks.md`](../../docs/sdks.md).


## About

This TypeScript SDK client supports the [Fetch API](https://fetch.spec.whatwg.org/)
and is automatically generated by the
[OpenAPI Generator](https://openapi-generator.tech) project:

- API version: `0.4.0`
- Package version: `0.4.2`
- Generator version: `7.22.0`
- Build package: `org.openapitools.codegen.languages.TypeScriptFetchClientCodegen`

The generated npm module supports the following:

- Environments
  * Node.js
  * Webpack
  * Browserify
- Language levels
  * ES5 - you must have a Promises/A+ library installed
  * ES6
- Module systems
  * CommonJS
  * ES6 module system


## Development

### Building

To build the TypeScript source code, you need to have Node.js and npm installed.
After cloning the repository, navigate to the project directory and run:

```bash
npm install
npm run build
```

### Package availability

Package availability is a release-process fact and is not implied by this
generated reference. Verify availability outside this README before documenting
install commands for users.

## License

[]()
