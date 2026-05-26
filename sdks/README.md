# MFS SDKs

Programmatic clients for the MFS HTTP `/v1` control plane, generated from
[`protocol/openapi.yaml`](../protocol/openapi.yaml). Regenerate with
[`./generate.sh`](generate.sh) after the spec changes.

| Language | Dir | Generator | Install |
|---|---|---|---|
| Python | [`python/`](python) | `python` (urllib3) | `pip install mfs-sdk` |
| TypeScript | [`typescript/`](typescript) | `typescript-fetch` | `npm install @mfs/sdk` |

APIs are grouped by tag: `ServerApi` (info/status), `IngestApi` (add/job),
`RetrievalApi` (search/grep), `BrowseApi` (ls/cat). All return the typed result
envelope (`ResultEnvelope`: source / lines / content / score / locator / metadata).

## Verified

Each SDK has a smoke test exercised against a live server (search→envelope, ls,
cat, status, error mapping):

- Python: `sdks/smoke/python/smoke_test.py` — 10/10
- TypeScript: `sdks/smoke/typescript/smoke_test.cjs` — 9/9
