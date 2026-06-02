# linear connector — ingest

URI: `linear://<alias>`.

## How to obtain credentials

A **Personal API key** from Linear:

1. <https://linear.app> → **Settings → Account → API → Personal API
   keys**.
2. **Create new key** → name `mfs` → copy. Starts with `lin_api_…`.

The key inherits the user's workspace access (all teams + projects
they can see in the UI).

## Required toml fields

| key | what |
|---|---|
| `api_key` | the key (`env:LINEAR_API_KEY` recommended) |

## Optional

| key | meaning |
|---|---|
| `teams` | team IDs to filter to (empty = all teams) |

Team IDs are UUID-like; find them via:
```bash
curl -H "Authorization: $LINEAR_API_KEY" \
  -H "Content-Type: application/json" \
  https://api.linear.app/graphql \
  -d '{"query":"{ teams { nodes { id key name } } }"}'
```

## URI tree

```
linear://<alias>/
└── teams/
    ├── ENG/issues.jsonl
    ├── OPS/issues.jsonl
    └── ...
```

Each `issues.jsonl` is a record collection. Linear issues carry
description (markdown) + comments + linked PRs.

## env: example

```toml
api_key = "env:LINEAR_API_KEY"
teams = ["ENG", "OPS"]
```

```bash
export LINEAR_API_KEY=lin_api_...
mfs add linear://acme --config /tmp/mfs-linear.toml
```

## Pitfalls

- **GraphQL rate limit**: ~1500 req/hour. Large workspaces ingest
  slowly on first sync.
- **Archived issues**: included by default. The connector doesn't
  filter on `archivedAt` — if you need active-only, post-filter at
  search time.
- **Team key vs team UUID**: Linear's `teams[].id` field uses UUIDs in
  the API, but humans often refer to teams by their key (e.g. `ENG`).
  The connector accepts both in `teams = [...]`.
