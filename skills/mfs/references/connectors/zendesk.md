# zendesk connector (`zendesk://`)

## What this is

Zendesk Support — tickets, ticket comments, users, organizations. Uses
Zendesk REST v2 directly via `httpx` (no SDK — the API is small).
Cursor-paginated streams.

**When MFS helps**: a support org with thousands of tickets + comment
threads — "any past escalations about saml SSO assertion failures" — across
ticket bodies AND every individual comment.

## URI shape

```
zendesk://<alias>/                                       connector root
zendesk://<alias>/tickets/records.jsonl                  all tickets (lazy)
zendesk://<alias>/tickets/comments.jsonl                 all comments (each tagged with ticket_id)
zendesk://<alias>/tickets/schema.json                    ticket field metadata
zendesk://<alias>/users/records.jsonl                    all end-users + agents
zendesk://<alias>/organizations/records.jsonl            organizations
```

object_kind for all `.jsonl` is `record_collection`.

## Auth — email + API token (basic)

Zendesk's REST API uses HTTP basic auth in a special form:
`Authorization: Basic base64(<email>/token:<api_token>)`. The connector
takes the two pieces separately:

```toml
subdomain = "acme"                # acme.zendesk.com → "acme"
email     = "support-mfs@acme.com"
credential_ref = "env:ZENDESK_TOKEN"     # the API token, not the password
```

Where to create: Zendesk Admin Center → "Apps and integrations" → "APIs"
→ Zendesk API → enable "Token access" → "+ Add API token", copy and save.

## Connector config TOML

```toml
# ─── auth (required) ───
subdomain = "acme"                          # the bit before .zendesk.com
email     = "support-mfs@acme.com"
credential_ref = "env:ZENDESK_TOKEN"

# ─── optional ───
# base_url = "https://support.acme.com"     # for custom domains; otherwise auto-built
# max_read_rows = 50000                      # cap per collection

# ─── per-collection field mapping (preset 'zendesk.tickets' applied automatically) ───
[[objects]]
match           = "/tickets/records.jsonl"
text_fields     = ["subject", "description"]
metadata_fields = ["status", "priority", "tags[*]", "updated_at"]
locator_fields  = ["id"]

[[objects]]
match           = "/tickets/comments.jsonl"
text_fields     = ["body"]
metadata_fields = ["public", "created_at", "author_id"]
locator_fields  = ["id", "ticket_id"]
```

## What each command does

| Command | Behaviour |
|---|---|
| `mfs ls /` | lists `tickets/`, `users/`, `organizations/`. |
| `mfs cat /tickets/records.jsonl --range A:B` | cursor-paged `tickets?page[size]=100`. |
| `mfs cat /tickets/records.jsonl --locator '{"id":123}'` | `GET /api/v2/tickets/123`. |
| `mfs cat /tickets/comments.jsonl --locator '{"id":888}'` | `GET /api/v2/tickets/<ticket>/comments/888`. |
| `mfs cat /tickets/schema.json` | `GET /api/v2/ticket_fields`. |
| `mfs grep "PATTERN" /tickets/records.jsonl` | linear scan; Zendesk Search API not used. |
| `mfs search "QUERY"` | Milvus only. Hits split between ticket-level and comment-level chunks. |

## Typical workflow

```bash
# 1. Create an API token in Admin Center.
export ZENDESK_TOKEN="..."

# 2. Register.
cat > zendesk-acme.toml <<'EOF'
subdomain = "acme"
email = "support-mfs@acme.com"
credential_ref = "env:ZENDESK_TOKEN"
EOF
mfs add zendesk://acme --config zendesk-acme.toml

# 3. Search across tickets + comments simultaneously.
mfs search "saml assertion signature invalid" --connector-uri zendesk://acme
# hit might be a comment, not the ticket itself:
#   zendesk://acme/tickets/comments.jsonl  locator: {"id":888, "ticket_id":12345}
mfs cat zendesk://acme/tickets/comments.jsonl --locator '{"id":888,"ticket_id":12345}'
# Then read the parent ticket for context:
mfs cat zendesk://acme/tickets/records.jsonl --locator '{"id":12345}'

# 4. Refresh.
mfs add zendesk://acme --no-full
```

## Incremental sync

Per-collection fingerprint = `count | max(updated_at)`. Cursor-paged refresh
uses `start_time` / `cursor` parameters as documented by Zendesk's Incremental
Export endpoints where available; otherwise standard `page[after]` cursor.

Comments are pulled per-ticket — if you have a ticket with 200 comments and
it gets updated, all 200 comments re-fetch on next sync.

## Gotchas

1. **Token != password**. The `credential_ref` holds the **API token**
   (created in Admin Center). The connector sends `<email>/token:<api-token>`
   as basic-auth user/pass — the suffix `/token` after the email is the
   Zendesk convention.
2. **`subdomain` not full URL**. `subdomain = "acme"`, NOT `"acme.zendesk.com"`.
   If you have a custom branded domain (CNAME'd), set `base_url` instead.
3. **Public vs private comments**: by default the connector includes both.
   To exclude private (internal) comments, filter at the source — Zendesk
   has no API flag to scope by `public=true` on the comments stream; the
   alternative is to skip indexing `/tickets/comments.jsonl` entirely and
   rely on the ticket-level `description` only.
4. **Tag access control**: the API returns only tickets the authenticated
   user has access to. For agent tokens this is usually "all"; for restricted
   roles it's narrower.
5. **Rate limits**: Zendesk's per-endpoint rate is conservative
   (~200 req/min). Initial syncs of huge orgs (>50k tickets) take time.
