# MFS SDKs

Programmatic clients for the MFS HTTP `/v1` control plane, generated from
[`protocol/openapi.yaml`](../protocol/openapi.yaml). Regenerate with
[`./generate.sh`](generate.sh) after the spec changes.

| Language | Dir | Generator | Install |
|---|---|---|---|
| Python | [`python/`](python) | `python` (urllib3) | `pip install mfs-sdk` |
| TypeScript | [`typescript/`](typescript) | `typescript-fetch` | `npm install @mfs/sdk` |
| Go | [`go/`](go) | `go` | `go get github.com/zilliztech/mfs-sdk-go` |
| Java | [`java/`](java) | `java` (okhttp-gson) | `io.zilliz:mfs-sdk` |

APIs are grouped by tag: `ServerApi` (info/status), `IngestApi` (add/job),
`RetrievalApi` (search/grep), `BrowseApi` (ls/cat). All return the typed result
envelope (`ResultEnvelope`: source / lines / content / score / locator / metadata).

## Verified

Each SDK has a smoke test exercised against a live server (search→envelope, ls,
cat, status, error mapping):

- Python: `sdks/python/smoke_test.py` — 10/10
- TypeScript: `sdks/typescript/smoke_test.cjs` — 9/9
- Go: `sdks/go-smoke/` — 9/9
- Java: `sdks/java-smoke/` — 9/9
