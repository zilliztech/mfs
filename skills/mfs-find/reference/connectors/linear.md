# linear connector — search & browse

## URI tree

```
linear://<alias>/
└── teams/
    ├── ENG/issues.jsonl
    └── OPS/issues.jsonl
```

## Record shape

```json
{"id": "ABC-12345",
 "title": "Auth bug",
 "description": "Markdown content...",
 "state": "In Progress",
 "priority": 2,
 "assignee": {"name": "alice", "email": "..."},
 "labels": [{"name": "bug"}],
 "comments": [{"user": {"name": "bob"}, "body": "..."}, ...],
 "updatedAt": "2026-06-01T..."}
```

## Chunk kind

`row_text` — content from `title + description + comments[].body`.

## Locator

```bash
mfs cat linear://<alias>/teams/ENG/issues.jsonl --locator '{"id": "ABC-12345"}'
```

Linear's id is the human-readable team-key + number form (e.g.
`ENG-42`).

## Search strategy

| Intent | Use |
|---|---|
| Find issues about X | `mfs search "X" linear://<alias>` |
| Team scope | `mfs search "X" linear://<alias>/teams/ENG/issues.jsonl` |

## Pitfalls

- **Archived issues included by default**: client-side filter on
  `metadata.state` if you want active only.
- **GraphQL rate limit ~1500 req/hour**: big workspaces ingest slowly.
- **No team filter → all teams**: if `teams = []` in the toml, all are
  enumerated. Restrict if your workspace has many.
