# Linear (`linear`)

The `linear` connector indexes Linear issues by team, plus the user directory,
over Linear's GraphQL API.

## How MFS sees it

```text
linear://workspace/
├── teams/
│   └── ENG/issues.jsonl     record_collection
└── users.jsonl              record_collection
```

Built-in presets cover both objects: `linear.issues` embeds issue title and
description (with state/priority/assignee/labels as metadata, `identifier` as the
locator), and `linear.users` embeds name and email. No `[[objects]]` config is
needed unless you want different fields.

## Credentials

A **Personal API key** from Linear:

1. <https://linear.app> → *Settings → Personal → Security & access* →
   *Personal API keys* → *New API key*.
2. Name it `mfs` and choose the permission and team access that covers the teams
   you'll sync.
3. Copy the value (`lin_api_…`).

The key is tied to the issuing user. Broad team access lets MFS enumerate all
teams that user can see; restricted access must include every team you list in
`teams`.

## Configuration

```toml
api_key = "env:LINEAR_API_KEY"
teams = ["ENG"]               # empty = all visible teams
```

## Sync and freshness

The connector uses the issue `updatedAt` field as its cursor for incremental
re-sync; deletions are caught by `full_scan`.

## Search and browse

```bash
mfs add linear://workspace --config ./linear.toml

mfs search "billing migration" linear://workspace/teams/ENG/issues.jsonl
mfs cat linear://workspace/teams/ENG/issues.jsonl --locator '{"identifier":"ENG-42"}'
```

## Pitfalls

- The API key is sent as the raw `Authorization` header value, not
  `Bearer <token>`.
- Omitting `teams` enumerates all visible teams; a team listed in TOML but outside
  the key's scope appears empty.
- The flattened issue record carries `identifier`, not an `id` field.
