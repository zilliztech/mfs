# Ingest troubleshooting

Common failures and recovery actions when `mfs add` doesn't end in a
clean `succeeded` + content searchable. Read this when:

- a job ends `failed`
- a job ends `succeeded` but `succeeded_objects == 0`
- `mfs search` returns nothing on a freshly-`available` connector
- `mfs add` itself errors before queueing a job

## A. `mfs add` returns HTTP error (before any job runs)

The control-plane couldn't even queue the work. Common cases:

| HTTP | code | Likely cause | Fix |
|---|---|---|---|
| 401 | `unauthorized` | `MFS_API_TOKEN` missing / wrong | `export MFS_API_TOKEN=$(cat ~/.mfs/server.token)` |
| 422 | `validation_error` | the toml has an unknown field or wrong type | check `reference/connectors/<scheme>.md` for the exact field set |
| 400 | `bad_request` | URI shape wrong, scheme unsupported | confirm `<scheme>://<alias>` form; valid schemes: postgres, mysql, slack, ... (full list in skill description) |
| 409 | `conflict` | a sync is already running for this URI | wait or `mfs job cancel <id>` first |
| 502 | `connector_unhealthy` | the engine tried to instantiate the plugin and it failed (bad DSN, network, missing extra) | read the error detail; pip-install the connector extra if missing (`uv sync --extra slack`, etc.) |
| 500 | `internal_error` | server bug | check server log: `mfs-server` stdout. Surface to user. |

## B. Job ends `failed`

```bash
mfs job get <job_id> --json
```

Look at `error.detail`. Common patterns:

### Auth failure

```
'auth failed: 401 Unauthorized' / 'invalid_credentials' / 'token revoked'
```

- The token / DSN is stale. → §D in SKILL.md (edit existing config).
- Slack: token may need re-OAuth. Discord: bot might have been kicked
  from the guild.
- Postgres: pg_hba might block the source IP. Have user check `psql`
  directly first.

### Permission / scope insufficient

```
'missing scope: channels:history' / 'permission denied for table X' /
'403 Forbidden'
```

- Token doesn't have the right scope. Check the matching
  `reference/connectors/<scheme>.md` "Required scopes" section.
- For SaaS connectors, this often requires re-issuing the token with
  the larger scope set, OR for the user to ask their admin.

### Network / unreachable

```
'connection refused' / 'name or service not known' / 'timeout'
```

- Connector trying to talk to a host the server can't reach. Common
  when: server in docker, source on `localhost` of the user's machine
  (containers don't share localhost by default).
- Workaround: use `host.docker.internal` (Mac/Win) or set `network_mode:
  host` on the docker compose. Or move the source to a reachable
  network.

### Schema / source mismatch

```
'table not found' / 'channel not found' / 'repo not found'
```

- The object the connector expected to read isn't there. Source may
  have been renamed / deleted, OR the toml's match pattern is wrong.
- For DB connectors: ASK user to `psql -c '\dt'` (or equivalent) to
  list actually-present tables.
- For GitHub: confirm owner/repo, confirm token has access.

### Rate limit

```
'rate limit exceeded' / '429' / 'too many requests'
```

- The connector hit the source's rate limit during enumeration.
- Action: wait and retry (`mfs add <uri>` again — the connector keeps
  its cursor and resumes). For chronic cases, lower `max_read_rows` or
  enable `--since` incremental syncs.

### Embedding API failure

```
'OpenAI API error: 401' / 'rate_limit_exceeded' / 'context_length_exceeded'
```

- Embedding side, not connector side. Check server's
  `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` etc.
- For `context_length_exceeded`: a single chunk is too long. Likely a
  giant row in a DB record_collection — lower `chunk_max` for that
  table, or trim `text_fields` to fewer columns.

## C. Job ends `succeeded` but `succeeded_objects == 0`

The connector connected fine but found nothing to index.

Walk this ladder:

```bash
# 1. is the connector seeing any objects at all?
mfs ls <uri>
```

- Empty → source is genuinely empty (new postgres DB with no tables,
  Slack channel the bot wasn't invited to, GitHub repo with no
  issues/PRs/files). Confirm with user.
- Has entries → step 2.

```bash
# 2. are the entries marked indexable?
mfs ls <uri> --json | jq '.entries[] | {name, indexable, search_status}'
```

- All `indexable: false` → toml might have an `[[objects]] indexable =
  false` rule that's too broad. Check the toml.
- All `search_status: not_indexable` → object kind isn't handled (e.g.
  binary blob with no converter). Skip those, confirm content actually
  searchable.
- Some indexable, some indexed → that's normal; sync may be partial.

```bash
# 3. is the right field configured for content?
# For DB / SaaS connectors, no text_fields → no content extracted → no chunks.
mfs cat <uri>/<sample-object> --range 0:3
```

- If the records are visible but `text_fields` is empty in the toml,
  add `[[objects]] text_fields = ["<column>"]`.

## D. Search returns nothing on a freshly-`available` connector

This is the most common "I did everything right but nothing's there"
case. Walk:

```bash
mfs status <uri>                  # should say 'available'
mfs ls <uri> --json | head        # what's actually indexed
mfs head <uri>/<one-object> -n 3  # is content where we think it is?
```

Then try a search with the literal text from `head`:

```bash
mfs search "<exact phrase visible in head output>" <uri> --top-k 5 --mode keyword
```

- If keyword finds it but hybrid/semantic doesn't → embedding model
  isn't seeing the field as relevant (rare; usually means text was
  rendered with too much noise — see `_render_record` rules).
- If keyword also misses → the field isn't in the chunk content.
  Re-check `text_fields` config.

For chat connectors specifically (slack/discord/feishu): chunks are
**thread-aggregated**, so searching for a single message's text might
find the thread but not as the top hit. Search the topic or surrounding
context instead.

## E. Server is up but tells me "no embedding provider available"

```bash
mfs server-info --json | jq .embedding
```

If `provider` is `openai` but no `OPENAI_API_KEY` is set in the
server's env, every ingest job will fail. Fix on the server side:

```bash
# wherever mfs-server runs:
export OPENAI_API_KEY=sk-...
mfs-server reload
```

Or run `mfs-server setup --section embedding` to switch to the local
ONNX default (no key required).

## F. Server config issue (rare but real)

User can't run `mfs add` for any URI; everything 502s. Check whether
the server itself is healthy:

```bash
mfs server-info             # 200 OK?
mfs status                  # Milvus connection green?
```

If Milvus is the problem: `MILVUS_URI` / `ZILLIZ_URI` likely wrong or
unreachable. The server logs the connection attempt at startup —
ask the user for the log line.

## When to escalate to the user vs auto-retry

| Symptom | Skill action |
|---|---|
| transient (timeout, 5xx, rate limit) | suggest one retry of `mfs add <uri>` |
| persistent auth failure | gather error detail, ASK user to update credentials, then §D |
| persistent schema mismatch | ASK user to verify source state (table/channel/repo exists, right name) |
| `succeeded` but `succeeded_objects == 0` | walk §C with user, never auto-`--full` |
| server-level issue (embedding key missing, Milvus down) | escalate; this skill can't fix server config remotely |
