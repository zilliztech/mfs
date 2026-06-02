# jira connector — search & browse

## URI tree

```
jira://<alias>/
└── projects/
    ├── ENG/issues.jsonl
    ├── OPS/issues.jsonl
    └── ...
```

One `issues.jsonl` per project.

## Record shape

```json
{"number": "ENG-1234",
 "summary": "Auth bug in SSO",
 "description": "When users...",
 "status": "Open",
 "priority": "High",
 "issuetype": "Bug",
 "labels": ["sso", "auth"],
 "reporter": "alice@acme.com",
 "assignee": "bob@acme.com",
 "comments": [{"author": "...", "body": "...", "created": "..."}, ...],
 "updated_at": "2026-06-01T...",
 "customfield_12345": "..."}
```

Custom fields appear with their internal `customfield_*` IDs.

## Chunk kind

`row_text` — content combines `summary + description + comments[].body`.

## Locator

```bash
mfs cat jira://<alias>/projects/ENG/issues.jsonl --locator '{"number": "ENG-1234"}'
```

Note: Jira issue keys (e.g. `ENG-1234`) are unique per project, used
as the locator key.

## Search strategy

| Intent | Use |
|---|---|
| Find past tickets about X | `mfs search "X" jira://<alias>` |
| Scope to a project | `mfs search "X" jira://<alias>/projects/ENG/issues.jsonl` |
| Active tickets only | search + client-side filter on `metadata.status` (no native filter) |
| Recent activity | `mfs search "X" jira://<alias> --top-k 30` + sort by `metadata.updated_at` client-side |

## Field semantics

- `status` values are project-specific (`Open`, `To Do`, `In Progress`,
  `Closed`, `Won't Fix`, custom).
- `issuetype` is one of the workflow types.
- `comments[].author` is the user's displayName or email depending on
  Jira's privacy settings.
- Custom fields: the connector exposes them by their internal name
  (`customfield_12345`). The human-friendly names are project-specific
  and not auto-resolved.

## Pitfalls

- **Identifying custom fields**: `mfs head jira://<alias>/projects/ENG/issues.jsonl
  -n 1` shows one issue's full structure. Map `customfield_NNNNN` to a
  human name via Jira admin.
- **Big tenants without `projects` filter**: a fresh sync may pull all
  projects → 100k+ issues. Watch for `partial`.
- **`enhanced_jql` (Cloud) vs classic**: the connector uses
  enhanced_jql on Cloud (better pagination); Server/DC falls back.
- **Permission scope is the user's**: tickets in restricted projects
  appear empty if the API token user doesn't have access.
