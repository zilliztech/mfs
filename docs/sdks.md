# SDKs

MFS includes checked-in Python and TypeScript clients generated from the
OpenAPI contract. Use this page when you want typed client calls for the common
server, ingest, retrieval, and browse workflows. Use the [HTTP API](api.md) page
for the full `/v1` endpoint contract and runtime behavior.
Use [Development](development.md#openapi-to-sdks) when you are changing the
OpenAPI spec or regenerating the checked-in SDK sources.

!!! warning "Generated-client coverage"
    The current generated clients expose `BrowseApi`, `IngestApi`,
    `RetrievalApi`, and `ServerApi`. The OpenAPI spec also contains operations
    that are not present in those generated API classes, including connector
    management, file manifest/upload, `head`, `tail`, `export`, and `listJobs`.
    Call the HTTP API directly, or regenerate and inspect the SDK sources, before
    relying on generated methods for those operations.

## Packages

| Language | Package name | Import | Checked-in version | Generator |
|---|---|---|---|---|
| Python | `mfs_sdk` | `import mfs_sdk` | `0.4.0` | OpenAPI Generator `python` client with `urllib3` |
| TypeScript | `@mfs/sdk` | `import { ... } from "@mfs/sdk"` | `0.4.0` | OpenAPI Generator `typescript-fetch` client |

Generated source docs:

| Language | Package README | API/model reference entry |
|---|---|---|
| Python | [`sdks/python/README.md`](https://github.com/zilliztech/mfs/blob/main/sdks/python/README.md) | [`sdks/python/docs/README.md`](https://github.com/zilliztech/mfs/blob/main/sdks/python/docs/README.md) |
| TypeScript | [`sdks/typescript/README.md`](https://github.com/zilliztech/mfs/blob/main/sdks/typescript/README.md) | [`sdks/typescript/docs/README.md`](https://github.com/zilliztech/mfs/blob/main/sdks/typescript/docs/README.md) |

!!! note "Set the base URL and token yourself"
    The SDKs are generated from `protocol/openapi.yaml`. Always point the client
    at your running server's base URL and send a bearer token when the server has
    auth enabled — the running server is authoritative for auth behavior. The
    checked-in READMEs add a short runtime overlay before the generated examples.

## Base URL and Auth

Set the base URL explicitly. The generated clients default to
`http://127.0.0.1:8765`, while `mfs-server run` and `mfs-server api` default to
`127.0.0.1:13619`.

When the server is configured with `auth_token`, every request except
`GET /healthz` must include:

```text
Authorization: Bearer <token>
```

`mfs-server run` and `mfs-server api` bootstrap a token by reusing or creating
`$MFS_HOME/server.token` unless the server is explicitly configured with
`auth_token = "-"`.

| Language | Base URL setup | Bearer-token setup |
|---|---|---|
| Python | `mfs_sdk.Configuration(host=base_url)` | `api_client.set_default_header("Authorization", f"Bearer {token}")` |
| TypeScript | `new Configuration({ basePath })` | `new Configuration({ headers: { Authorization: "Bearer ..." } })` |

## API Mapping

| Workflow | HTTP endpoint | Python method | TypeScript method |
|---|---|---|---|
| Server info | `GET /v1/server/info` | `ServerApi.get_server_info()` | `ServerApi.getServerInfo()` |
| Server status | `GET /v1/status` | `ServerApi.status()` | `ServerApi.status()` |
| Add or enqueue a source | `POST /v1/add` | `IngestApi.add_source(add_request)` | `IngestApi.addSource({ addRequest })` |
| Upload a source archive | `POST /v1/upload` | `IngestApi.upload_source(...)` | `IngestApi.uploadSource(...)` |
| Poll one job | `GET /v1/jobs/{job_id}` | `IngestApi.get_job(job_id)` | `IngestApi.getJob({ jobId })` |
| Cancel one job | `POST /v1/jobs/{job_id}/cancel` | `IngestApi.cancel_job(job_id)` | `IngestApi.cancelJob({ jobId })` |
| Search indexed content | `GET /v1/search` | `RetrievalApi.search(q=..., top_k=...)` | `RetrievalApi.search({ q, topK })` |
| Grep a path | `GET /v1/grep` | `RetrievalApi.grep(pattern=..., path=...)` | `RetrievalApi.grep({ pattern, path })` |
| List a path | `GET /v1/ls` | `BrowseApi.ls(path=...)` | `BrowseApi.ls({ path })` |
| Read an object | `GET /v1/cat` | `BrowseApi.cat(path=..., range=..., meta=..., density=...)` | `BrowseApi.cat({ path, range, meta, density })` |

## Minimal Examples

Set `MFS_URL` to the running server and set `MFS_TOKEN` when that server
requires bearer auth:

```bash
export MFS_URL=http://127.0.0.1:13619
export MFS_TOKEN=replace-with-your-token
```

Python:

```python
import os

import mfs_sdk


base_url = os.getenv("MFS_URL", "http://127.0.0.1:13619")
token = os.getenv("MFS_TOKEN")
target = os.getenv("MFS_TARGET", "/tmp/mfs_sdk_fixture")

configuration = mfs_sdk.Configuration(host=base_url)

with mfs_sdk.ApiClient(configuration) as client:
    if token:
        client.set_default_header("Authorization", f"Bearer {token}")

    server = mfs_sdk.ServerApi(client)
    retrieval = mfs_sdk.RetrievalApi(client)
    browse = mfs_sdk.BrowseApi(client)
    ingest = mfs_sdk.IngestApi(client)

    print(server.get_server_info().version)

    search = retrieval.search(q="single sign-on login", top_k=3)
    for hit in search.results:
        print(hit.source, hit.score)

    listing = browse.ls(path=target)
    print([entry.name for entry in listing.entries])

    content = browse.cat(path=f"{target}/auth.md")
    print(content)

    job = ingest.add_source(mfs_sdk.AddRequest(target=target, process=False))
    job_status = ingest.get_job(job.job_id)
    print(job_status.id, job_status.status)
```

TypeScript:

```ts
import {
  BrowseApi,
  Configuration,
  IngestApi,
  RetrievalApi,
  ServerApi,
} from "@mfs/sdk";

const basePath = process.env.MFS_URL ?? "http://127.0.0.1:13619";
const token = process.env.MFS_TOKEN;
const target = process.env.MFS_TARGET ?? "/tmp/mfs_sdk_fixture";

const configuration = new Configuration({
  basePath,
  headers: token ? { Authorization: `Bearer ${token}` } : undefined,
});

const server = new ServerApi(configuration);
const retrieval = new RetrievalApi(configuration);
const browse = new BrowseApi(configuration);
const ingest = new IngestApi(configuration);

async function main() {
  const info = await server.getServerInfo();
  console.log(info.version);

  const search = await retrieval.search({ q: "single sign-on login", topK: 3 });
  for (const hit of search.results) {
    console.log(hit.source, hit.score);
  }

  const listing = await browse.ls({ path: target });
  console.log(listing.entries.map((entry) => entry.name));

  const content = await browse.cat({ path: `${target}/auth.md` });
  console.log(content);

  const job = await ingest.addSource({
    addRequest: { target, process: false },
  });
  const jobStatus = await ingest.getJob({ jobId: job.jobId });
  console.log(jobStatus.id, jobStatus.status);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
```

## Regenerate

Regenerate clients after `protocol/openapi.yaml` changes. The script requires
Java 11+ and `openapi-generator-cli`.

```bash
npm install -g @openapitools/openapi-generator-cli
cd sdks
./generate.sh
```

The script regenerates only:

- `sdks/python/` with package name `mfs_sdk`
- `sdks/typescript/` with npm name `@mfs/sdk`

After regeneration, check the generated API classes, package metadata, generated
README files, and this page before documenting any new method names or fields.

## Smoke Tests

Smoke tests live under `sdks/smoke/`. They are repository test harnesses, not
package entry points or package availability evidence. They run against a live
server on `127.0.0.1:8765` after a small fixture has been added, and cover
search-to-envelope, `ls`, `cat`, `status`, and error mapping.

Current harness scope:

| Language | Harness | Checks |
|---|---|---|
| Python | `sdks/smoke/python/smoke_test.py` | 10 |
| TypeScript | `sdks/smoke/typescript/smoke_test.cjs` | 9 |

The current smoke scripts set only the generated client base URL. They do not
validate bearer-token setup unless you extend the harness with authorization
headers.

```bash
cd sdks/smoke
cd python && uv pip install -e ../../python && python smoke_test.py
```

```bash
cd sdks/smoke
(cd ../typescript && npm i && npm run build) && node typescript/smoke_test.cjs
```

## Related

- [HTTP API](api.md) for the full endpoint matrix, auth rules, curl examples,
  and runtime error envelope.
- [`protocol/openapi.yaml`](https://github.com/zilliztech/mfs/blob/main/protocol/openapi.yaml)
  for the source OpenAPI contract.
- [`sdks/generate.sh`](https://github.com/zilliztech/mfs/blob/main/sdks/generate.sh)
  for generator configuration and current language scope.
- [Development](development.md#openapi-to-sdks) for the local regeneration and
  smoke-test runbook.
