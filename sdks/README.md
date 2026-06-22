# MFS SDKs

Checked-in Python and TypeScript clients for the MFS HTTP `/v1` control plane,
generated from [`protocol/openapi.yaml`](../protocol/openapi.yaml). Regenerate
with [`./generate.sh`](generate.sh) after the spec changes.

> **Runtime safety note**
>
> These directories contain generated reference material plus small checked-in
> documentation overlays. Use [`docs/sdks.md`](../docs/sdks.md) and
> [`docs/api.md`](../docs/api.md) for the curated v0.4 runtime guidance.
> Normal `mfs-server run` / `mfs-server api` runs default to
> `http://127.0.0.1:13619` and may require `Authorization: Bearer <token>`.
> The generated client default `http://127.0.0.1:8765` is only the generator
> default and smoke-harness target unless you intentionally start a server there.

| Language | Directory | Package metadata | Generator | Reference entry |
|---|---|---|---|---|
| Python | [`python/`](python) | `mfs_sdk` `0.4.2` | `python` with `urllib3` | [`python/docs/README.md`](python/docs/README.md) |
| TypeScript | [`typescript/`](typescript) | `@mfs/sdk` `0.4.2` | `typescript-fetch` | [`typescript/docs/README.md`](typescript/docs/README.md) |

APIs are grouped by tag: `ServerApi` (info/status), `IngestApi` (add/job),
`RetrievalApi` (search/grep), `BrowseApi` (ls/cat). Search results use
`ResultEnvelope` (`source`, `content`, `score`, `locator`, `metadata`); current
runtime guidance treats line ranges as `locator.lines`.

The OpenAPI spec declares bearer security. If checked-in generated authorization
text still says "no authorization required", treat it as stale scaffolding. The
running server is authoritative for auth behavior.

## Smoke Harnesses

Smoke tests live under [`smoke/`](smoke). They are repository test harnesses,
not package availability evidence. The checked-in scripts run
against a live server on `127.0.0.1:8765` and cover search-to-envelope, `ls`,
`cat`, `status`, and error mapping:

- Python: `sdks/smoke/python/smoke_test.py` - 10 checks
- TypeScript: `sdks/smoke/typescript/smoke_test.cjs` - 9 checks
