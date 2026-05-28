# linear connector (`linear://`)

## What this is

Linear (the issue tracker) exposed as a filesystem tree. Each team's issues
are a lazy record stream. Uses Linear's GraphQL API directly (httpx) — no
official Python SDK exists, so the connector hand-rolls the queries against
documented schema fields.

**When MFS helps**: Linear orgs with many teams + thousands of issues —
"any team hit this stripe webhook signature issue?" — searched across all
teams at once.

## URI shape

```
linear://<alias>/                              connector root
linear://<alias>/teams/                        all teams (or a subset via config)
linear://<alias>/teams/ENG/                    one team
linear://<alias>/teams/ENG/issues.jsonl        lazy issue stream
linear://<alias>/users.jsonl                   workspace users
```

object_kind for `issues.jsonl` is `record_collection`. Each issue has
`identifier` (e.g. `"ENG-42"`), `title`, `description`, `priority`, `state`,
`assignee`, `labels`, `createdAt`, `updatedAt`.

## Auth

Personal API key. **Note**: Linear's auth header is the **raw key** in
`Authorization:`, NOT `Bearer <key>`. The connector handles this difference;
you just provide the key.

```toml
credential_ref = "env:LINEAR_API_KEY"
# value: "lin_api_xxx..." (a Personal API key)
```

Where to create: Linear → Settings → API → Personal API keys → "Create new
key". Scope is the whole workspace (Linear PAT permissions are
account-wide).

OAuth tokens would use `Authorization: Bearer <token>` — currently not
implemented; PAT only.

## Connector config TOML

```toml
# ─── auth (required) ───
credential_ref = "env:LINEAR_API_KEY"

# ─── scope ───
# teams = ["ENG", "OPS"]              # restrict to team keys; default = all teams
# max_read_rows = 5000                # cap per team; default 1000

# ─── per-team field mapping ───
[[objects]]
match           = "/teams/*/issues.jsonl"
text_fields     = ["title", "description"]
metadata_fields = ["state", "priority", "labels[*]", "updatedAt"]
locator_fields  = ["identifier"]
```

## What each command does

| Command | Behaviour |
|---|---|
| `mfs ls /teams/` | GraphQL `teams { nodes { key } }` filtered by `teams` config. |
| `mfs ls /teams/<key>/` | `["issues.jsonl"]`. |
| `mfs cat /teams/<key>/issues.jsonl` | **refused** (lazy). |
| `mfs cat .../issues.jsonl --range A:B` | paginated GraphQL `issues(first, after)` from cursor. |
| `mfs cat .../issues.jsonl --locator '{"identifier":"ENG-42"}'` | GraphQL `issue(id: ...)` by identifier. |
| `mfs cat /users.jsonl` | full workspace user list. |
| `mfs grep "PATTERN" .../issues.jsonl` | linear scan; no pushdown (Linear's text search is over indexed fields only). |
| `mfs search "QUERY"` | Milvus only. `row_text` per issue with `{identifier}` locator. |

## Typical workflow

```bash
# 1. Create a Personal API key.
export LINEAR_API_KEY="lin_api_xxx..."

# 2. Register.
cat > linear-acme.toml <<'EOF'
credential_ref = "env:LINEAR_API_KEY"
teams = ["ENG", "OPS"]
EOF
mfs add linear://acme --config linear-acme.toml

# 3. Search.
mfs search "stripe webhook signature verification" --connector-uri linear://acme
# hit: linear://acme/teams/ENG/issues.jsonl  locator: {"identifier":"ENG-241"}
mfs cat linear://acme/teams/ENG/issues.jsonl --locator '{"identifier":"ENG-241"}'

# 4. Refresh.
mfs add linear://acme --no-full
```

## Incremental sync

Per-team fingerprint = `count | max(updatedAt)`. GraphQL pagination uses
`pageInfo.endCursor` + `pageInfo.hasNextPage`. The refresh query asks for
issues with `updatedAt > <last_max>` ordered ascending, ingests until
caught up.

## Gotchas

1. **Auth header is NOT `Bearer`** — it's the raw key. If you build your
   own test scripts, mind this.
2. **PAT is workspace-wide**, no per-team scoping at the key level. The
   `teams` config field filters client-side.
3. **GraphQL complexity limits** — Linear caps query complexity. The
   connector keeps `first: 100` per page; deep nested expansions might
   hit the cap. Comments are NOT fetched by default (would balloon the
   query).
4. **`identifier` not `id`** — locators key on the human-readable
   `"ENG-42"` form, not the GraphQL node ID. `mfs cat --locator
   '{"identifier":"ENG-42"}'` is the right shape.
5. **No comment indexing today**. If you need comment threads searchable,
   that's a future enhancement (would mirror github's `comments[].body`).
6. **Free workspaces have rate-limited GraphQL** (~few req/sec). For
   large orgs use a paid workspace's higher quota.
