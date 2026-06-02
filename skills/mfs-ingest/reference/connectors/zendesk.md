# zendesk connector — ingest

URI: `zendesk://<alias>`.

## How to obtain credentials

Zendesk uses **email + API token** (the user's email gets the literal
`/token` suffix appended automatically by the auth layer).

1. <https://acme.zendesk.com> → **Admin Center → Apps and
   integrations → APIs → Zendesk API**.
2. Toggle **Token Access** ON.
3. **Add API token** → label `mfs` → copy.

The token is bound to your user account; it inherits your role's
permissions.

## Required toml fields

| key | what |
|---|---|
| `subdomain` | `acme` (the part before `.zendesk.com`) |
| `username` | user email |
| `api_token` | the token (`env:ZENDESK_API_TOKEN` recommended) |

## Optional

| key | default | meaning |
|---|---|---|
| `base_url` | `https://<subdomain>.zendesk.com` | override only for unusual deployments |
| `max_read_rows` | 100000 | per-resource cap |

## URI tree

```
zendesk://<alias>/
├── tickets.jsonl                      ← tickets
├── users.jsonl                        ← agents + end-users
├── organizations.jsonl                ← orgs
└── tickets_comments.jsonl             ← all comments (joined with ticket_id)
```

The `tickets_comments` virtual collection joins comments to their
parent tickets, so a search for "what did agent X say about this" finds
the conversation surface.

## env: example

```toml
subdomain = "acme"
username = "alice@acme.com"
api_token = "env:ZENDESK_API_TOKEN"
max_read_rows = 50000
```

```bash
export ZENDESK_API_TOKEN=...
mfs add zendesk://acme --config /tmp/mfs-zendesk.toml
```

## Pitfalls

- **Admin-only data**: tickets in restricted groups only appear to
  agents in those groups. Use a user with broad ticket-read permission
  if you want full coverage.
- **Suspended tickets**: filtered out by default.
- **Side-loaded relations**: Zendesk has nested relations (org of a
  user, brand of a ticket). The connector flattens common ones; deeper
  relations require post-processing.
- **Rate limit**: 700 req/min per user, 200 req/min on the search
  endpoint. Long ticket histories take a while.
