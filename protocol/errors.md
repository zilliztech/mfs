# MFS error codes (canonical)

The HTTP API and CLI surface a stable `code` plus human `detail` and optional
`suggestions`. Codes are additive-only within API `/v1` (design/10 §7). Clients and
SDKs should switch on `code`, never on `detail` text.

## Envelope

```json
{ "code": "object_too_large_for_cat", "detail": "...", "suggestions": ["use head", "cat --range"] }
```

## Codes

| code | HTTP | meaning | recommended action |
|---|---|---|---|
| `object_too_large_for_cat` | 400 | `cat` on a big object without `--range` | `head` / `cat --range` / `export` |
| `is_directory` | 400 | `cat` on a directory | `ls` / `tree` |
| `range_unsupported` | 400 | `--range` on binary/image | `--meta` or `export` |
| `density_unsupported` | 400 | `--peek/--skim/--deep` on a structured object | `head` / `cat --range` |
| `tail_unsupported` | 400 | object has no stable ordering | `head` / `cat --range` |
| `locator_not_found` | 404 | `cat --locator` key not present | re-search; record may be gone/changed |
| `chunk_max_exceeded` | 200* | object partially indexed (too large) | search works but recall partial; add `index_filter`/`windowed` or raise `chunk_max` |
| `since_unsupported` | 400 | `--since` on a connector without a time cursor | drop `--since` |
| `sync_already_running` | 409 | a sync is in flight | `status <uri>` / `job cancel` |
| `connector_removing` | 409 | connector being removed | wait, then retry |
| `connector_unhealthy` | 502 | source unreachable / bad creds | check credentials/connectivity |
| `embedding_quota_exceeded` | 502 | embedding API out of quota | top up, then re-add |
| `embedding_auth_failed` | 502 | bad embedding key | fix key, then re-add |
| `circuit_breaker_tripped` | 502 | too many consecutive fatal failures, job aborted | fix root cause (quota/auth), re-run add |
| `field_missing` | 400 | configured text_field absent | fix connector `[[objects]]` config |
| `upload_rejected` | 400 | `--no-upload` set | adjust flags or profile |
| `upload_not_applicable` | 400 | `--force-upload` on shared fs | adjust flags or profile |
| `not_found` | 404 | path / job / object not found | check the URI |
| `validation_error` | 422 | malformed request (FastAPI/pydantic) | fix request shape |

\* `chunk_max_exceeded` is surfaced as a `search_status: partial` rather than a hard
error; search still returns results with incomplete recall.

## Search availability (from `status`)

| value | meaning |
|---|---|
| `available` | index ready, use search |
| `partial` | search works but recall incomplete |
| `building` | indexing in progress, prefer grep/ls/cat |
| `unavailable` | nothing indexed (grep/cat only) |
