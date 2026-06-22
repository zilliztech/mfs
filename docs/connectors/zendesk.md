# Zendesk (`zendesk`)

The `zendesk` connector indexes support tickets and their comments, plus users
and organizations. Use it to search support history by meaning across every
ticket.

## How MFS sees it

```text
zendesk://acme/
├── tickets/
│   ├── records.jsonl      record_collection  (preset-indexed)
│   └── comments.jsonl     record_collection
├── users/records.jsonl
└── organizations/records.jsonl
```

The `zendesk.tickets` preset embeds ticket subject and description (status,
priority, tags as metadata). Comments, users, and organizations enumerate but
need `[[objects]]` rules to become searchable.

## Credentials

Zendesk uses **email + API token**. The auth layer appends the literal `/token`
suffix to the email automatically.

1. `https://<subdomain>.zendesk.com` → *Admin Center → Apps and integrations →
   APIs → Zendesk API*.
2. Toggle **Token Access** on.
3. *Add API token* → label it `mfs` → copy the value.

The token inherits the bound user's role permissions.

## Configuration

```toml
subdomain = "acme"
email = "alice@acme.com"
api_token = "env:ZENDESK_API_TOKEN"
max_read_rows = 50000
```

Save the file as `zendesk.toml`, then probe and index:

```bash
mfs connector probe zendesk://acme --config ./zendesk.toml
mfs add zendesk://acme --config ./zendesk.toml
```

## Sync and freshness

The connector uses each resource's `updated_at` field as its cursor for
incremental re-sync; deletions are caught by `full_scan`.

## Search and browse

```bash
mfs search "billing dispute" zendesk://acme/tickets/records.jsonl
mfs search "refund policy" zendesk://acme/tickets/comments.jsonl
mfs cat zendesk://acme/tickets/records.jsonl --locator '{"id":12345}'
```

## Pitfalls

- Only ticket records are preset-indexed; add `[[objects]]` for searchable
  comments, users, or organizations.
- Comments are fetched per ticket and can be expensive on large tenants.
- `max_read_rows` caps each resource path.
