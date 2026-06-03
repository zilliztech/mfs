# SDKs

MFS provides generated SDKs for programmatic clients.

| Language | Directory | Generator |
|---|---|---|
| Python | `sdks/python/` | OpenAPI Generator Python client |
| TypeScript | `sdks/typescript/` | OpenAPI Generator TypeScript fetch client |

Regenerate clients after OpenAPI changes:

```bash
cd sdks
./generate.sh
```

Smoke tests live under `sdks/smoke/` and exercise a live server. The SDK docs
should stay aligned with the HTTP API page and the generated README files.
