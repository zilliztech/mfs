# jira connector — ingest

URI: `jira://<alias>` (alias is your Jira tenant nickname).

## How to obtain credentials

Three flavours — pick based on your Jira deployment.

**Atlassian Cloud (most common)**:
- URL: `https://acme.atlassian.net`
- Username: your Atlassian account email
- API token: <https://id.atlassian.com/manage-profile/security/api-tokens>
  → **Create API token** → label it `mfs` → copy.

**Atlassian Server / Data Center (self-hosted)**:
- URL: `https://jira.acme.internal`
- Create a Personal Access Token from your Jira profile →
  **Personal Access Tokens** → Create.
- Put the PAT in the **`token`** field (not `api_token`), set `cloud = false`,
  and leave `username` empty. Setting `token` is what selects the PAT auth path.

**Older Server (no PAT support)**:
- Username + password basic auth (`username` + `api_token`), `cloud = false`.
  Discouraged but supported.

## Required toml fields

| key | what |
|---|---|
| `url` | full Jira base URL |
| `cloud` | `true` for Atlassian Cloud, `false` for Server / DC |
| auth (pick by deployment) | **Cloud**: `username` (email) + `api_token`. **Server / DC**: `token` (PAT). `env:` recommended for the secret. |

## Optional

| key | default | meaning |
|---|---|---|
| `projects` | _all_ | comma-separated project keys (e.g. `["ENG", "OPS"]`) |
| `max_read_rows` | 100000 | per-project issue cap |

## URI tree

```
jira://<alias>/
└── projects/
    ├── ENG/issues.jsonl
    ├── OPS/issues.jsonl
    └── ...
```

Each `issues.jsonl` is a record_collection — one chunk per issue with
summary + description + comments.

## env: example

```toml
url = "https://acme.atlassian.net"
cloud = true
username = "alice@acme.com"
api_token = "env:JIRA_API_TOKEN"
projects = ["ENG", "OPS"]
max_read_rows = 50000
```

```bash
export JIRA_API_TOKEN=...
mfs add jira://acme --config /tmp/mfs-jira.toml
```

Server / DC uses a PAT in `token` instead:

```toml
url = "https://jira.acme.internal"
cloud = false
token = "env:JIRA_PAT"
```

## Pitfalls

- **`enhanced_jql` API (Cloud only)**: the connector uses the paged
  enhanced JQL endpoint. Server / DC fall back to classic search.
- **No `projects` filter → all projects**: in a large tenant this is
  100k+ issues. ASK the user before running unfiltered on a big
  tenant. Run `mfs add jira://<alias> --config <toml>` without `--yes`
  so the CLI shows the automatic estimate-confirm prompt before queueing.
- **Permission errors per project**: the API token has the user's
  permission set; if some projects are restricted, those just return
  empty. Not a failure.
- **Custom fields**: Jira issues' custom fields appear in the JSON with
  IDs like `customfield_12345`. Use `mfs head jira://.../ENG/issues.jsonl`
  to see them.
- **Updated_at cursor**: incremental sync uses `updated >= last_seen`
  ordered by `updated ASC` — newly-edited old tickets get re-indexed.
