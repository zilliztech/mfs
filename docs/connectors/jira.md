# Jira (`jira`)

The `jira` connector indexes Jira issues by project, plus the user directory. A
search hit covers the whole issue conversation — summary, description, and
comments — so you find an issue by what was discussed, not just its title.

## How MFS sees it

```text
jira://acme/
├── projects/
│   ├── ENG/issues.jsonl     record_collection
│   └── OPS/issues.jsonl
└── users.jsonl              record_collection
```

The `jira.issues` preset embeds summary, description, and comment bodies, with
status/priority/assignee as metadata and `key` as the locator — so issues are
searchable without `[[objects]]` config, though you can override the fields.

## Credentials

Three flavours, by deployment:

- **Atlassian Cloud** (most common): URL `https://acme.atlassian.net`, username =
  your account email, and an API token from
  <https://id.atlassian.com/manage-profile/security/api-tokens> → *Create API
  token*.
- **Server / Data Center** (self-hosted): URL of your instance, username left
  empty, and a **Personal Access Token** from your Jira profile.
- **Older Server (no PAT)**: username + password basic auth (discouraged).

An API token inherits the issuing user's permissions — restricted projects look
empty if that user can't see them.

## Configuration

```toml
url = "https://acme.atlassian.net"
cloud = true
username = "alice@acme.com"
api_token = "env:JIRA_API_TOKEN"
projects = ["ENG", "OPS"]      # empty = all visible projects
max_read_rows = 50000
```

## Sync and freshness

The connector uses the issue `updated` timestamp as its cursor; Cloud retrieval
pages through `enhanced_jql`. Deletions are caught by `full_scan`.

## Search and browse

```bash
mfs add jira://acme --config ./jira.toml

mfs search "SSO regression" jira://acme/projects/ENG/issues.jsonl
mfs cat jira://acme/projects/ENG/issues.jsonl --locator '{"key":"ENG-1234"}'
```

## Pitfalls

- Without `projects`, the connector enumerates every visible project.
- The flattened record uses `key` as the issue-key field.
- Token permissions are the user's permissions; restricted projects appear empty.
