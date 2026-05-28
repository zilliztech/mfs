# jira connector (`jira://`)

## What this is

Atlassian Jira (Cloud or Server / Data Center). The connector uses
`atlassian-python-api`'s `Jira` client (sync, wrapped in
`asyncio.to_thread`). Each project's issues are exposed as a lazy record
stream; users are exposed separately.

**When MFS helps**: large issue trackers (5k+ open issues) where you want
semantic search across summaries + descriptions + comments — "anyone hit
this Redis timeout before?" — without manually browsing JQL.

## URI shape

```
jira://<alias>/                                connector root
jira://<alias>/projects/ENG/                   project folder
jira://<alias>/projects/ENG/issues.jsonl       lazy issue stream
jira://<alias>/users.jsonl                     users list
```

object_kind for `issues.jsonl` is `record_collection`. Each issue record
has `key`, `summary`, `description`, `status`, `priority`, `assignee`,
`reporter`, `labels`, `created`, `updated`, and optionally `comments[]`
(opt-in if you ask for them).

## Auth

Two flavours, both via `credential_ref`:

**Cloud** (atlassian.net) — username (email) + API token:

```toml
url      = "https://acme.atlassian.net"
username = "you@acme.com"
cloud    = true
credential_ref = "env:JIRA_TOKEN"      # the API token from id.atlassian.com → Security
```

**Server / Data Center** — PAT or basic auth:

```toml
url      = "https://jira.internal.acme/"
cloud    = false
credential_ref = "env:JIRA_TOKEN"      # personal access token
# Alternatively: username + password basic auth; credential_ref carries the password.
# username = "service.mfs"
```

Where to create tokens:
- **Cloud**: id.atlassian.com → Security → Create API token.
- **Server/DC**: Jira UI → your avatar → Profile → Personal Access Tokens.

## Connector config TOML

```toml
# ─── auth (required) ───
url   = "https://acme.atlassian.net"
cloud = true
username = "you@acme.com"            # cloud: email; server: usually your login name
credential_ref = "env:JIRA_TOKEN"

# ─── scope ───
# projects = ["ENG", "OPS"]          # restrict to these project keys; default = all visible
# include_comments = true            # opt-in to comments[].body in records
# max_read_rows = 5000               # cap per project; default 1000

# ─── per-project field mapping (preset 'jira.issues' applied automatically) ───
[[objects]]
match           = "/projects/*/issues.jsonl"
text_fields     = ["summary", "description"]  # add "comments[].body" if include_comments=true
metadata_fields = ["status", "priority", "labels[*]", "updated"]
locator_fields  = ["key"]
```

## What each command does

| Command | Behaviour |
|---|---|
| `mfs ls /projects/` | `jira.projects()` filtered by `projects` config. |
| `mfs ls /projects/<proj>/` | `["issues.jsonl"]`. |
| `mfs cat /projects/<proj>/issues.jsonl` | **refused** (lazy). |
| `mfs cat .../issues.jsonl --range A:B` | `jql("project = <proj> ORDER BY created", start=A, limit=B-A)`. |
| `mfs cat .../issues.jsonl --locator '{"key":"ENG-123"}'` | `jql("key = ENG-123")` — exact issue. |
| `mfs cat /users.jsonl` | full user list. |
| `mfs grep "PATTERN" .../issues.jsonl` | linear scan of fetched records (no pushdown — Jira's text search is JQL `text ~`). |
| `mfs search "QUERY"` | Milvus only. Returns `row_text` chunks per issue with `{key}` locator. |

## Typical workflow

```bash
# 1. Create an API token (cloud) or PAT (server/dc).
export JIRA_TOKEN="ATATT3xFf..."

# 2. Register.
cat > jira-acme.toml <<'EOF'
url      = "https://acme.atlassian.net"
cloud    = true
username = "you@acme.com"
credential_ref = "env:JIRA_TOKEN"
projects = ["ENG", "OPS"]
include_comments = true
EOF
mfs add jira://acme --config jira-acme.toml

# 3. Search and locate.
mfs search "redis cluster timeout" --connector-uri jira://acme --top-k 5
# hit:  jira://acme/projects/ENG/issues.jsonl  locator: {"key":"ENG-2104"}
mfs cat jira://acme/projects/ENG/issues.jsonl --locator '{"key":"ENG-2104"}'

# 4. Refresh.
mfs add jira://acme --no-full
```

## Incremental sync

Per-project fingerprint = `count | max(updated)`. The JQL the connector runs
to refresh is roughly
`project = <proj> AND updated > '<last_max_updated>' ORDER BY updated ASC`.
So issues moved to "Done" (or any field change that bumps `updated`) get
re-indexed; orphan deletions are detected by count drop.

## Gotchas

1. **Cloud token != password**. Don't put your Atlassian password as
   `credential_ref`. Use an API token from id.atlassian.com.
2. **`include_comments` costs extra requests** — one `issue.comments()`
   call per issue. For projects with thousands of long threads, expect
   minutes-of-sync overhead.
3. **JQL access control**: the token's user must have "Browse Project"
   on each project listed in `projects`. Unlisted projects are silently
   skipped if the user lacks permission.
4. **Custom fields** (`customfield_10001`, etc.) are passed through as-is
   in the record. To embed a custom field, add its raw key to `text_fields`.
5. **Server / Data Center auth**: `cloud = false` + a PAT in
   `credential_ref`. Basic auth (username + password) also works but is
   being phased out by Atlassian.
6. **Rate limits**: Jira Cloud has fairly generous limits; the connector
   does no backoff today — under heavy parallel use you may see 429s.
   Lower `max_read_rows` if you hit them.
