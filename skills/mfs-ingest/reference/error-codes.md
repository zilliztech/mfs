# Error codes & recovery

`--json` errors carry a stable `code` and `suggestions`. Act on `suggestions` first.

| code | meaning | do |
|---|---|---|
| `object_too_large_for_cat` | cat on a big object without `--range` | use `head` / `cat --range` / `export` |
| `is_directory` | cat on a directory | use `ls` / `tree` |
| `range_unsupported` | `--range` on binary/image | use `--meta` or `export` |
| `density_unsupported` | `--peek/--skim/--deep` on structured object | use `head` / `cat --range` |
| `tail_unsupported` | object has no stable ordering | use `head` / `cat --range` |
| `locator_not_found` | `cat --locator` key not present | re-search; the record may be gone/changed |
| `chunk_max_exceeded` | object partially indexed (too large) | `search` works but recall partial; add `index_filter`/`windowed` or raise `chunk_max` |
| `since_unsupported` | `--since` on a connector without time cursor | drop `--since` |
| `sync_already_running` | a sync is in flight | `mfs job list`, then wait or `mfs job cancel JOB_ID` |
| `connector_removing` | connector being removed | wait, then retry |
| `connector_unhealthy` | source unreachable / bad creds | check credentials/connectivity |
| `embedding_quota_exceeded` / `embedding_auth_failed` | embedding API out of quota / bad key | top up / fix key, then `mfs add` again |
| `circuit_breaker_tripped` | too many consecutive fatal failures, job aborted | fix the root cause (quota/auth), re-run `mfs add` |
| `field_missing` | configured text_field absent | fix connector `[[objects]]` config |
| `upload_rejected` / `upload_not_applicable` | `--no-upload` set / `--force-upload` on shared fs | adjust flags or profile |

Search availability (from `mfs status`): `available` → use search; `partial` →
search works but recall incomplete; `building` → wait or use grep; `unavailable`
→ nothing indexed (grep/cat only).
