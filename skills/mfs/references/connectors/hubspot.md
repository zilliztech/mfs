# hubspot connector (`hubspot://`)

## What this is

HubSpot CRM (contacts / companies / deals / tickets / custom objects).
Uses the official `hubspot-api-client` (sync, wrapped in
`asyncio.to_thread`). Each configured CRM object becomes a record stream.

**When MFS helps**: a large CRM with thousands of deals / tickets / notes
where you want "any tickets from $bigcustomer mentioning a renewal blocker"
without writing HubSpot's UI search query DSL.

## URI shape

```
hubspot://<alias>/                              connector root
hubspot://<alias>/contacts/records.jsonl        contacts (lazy)
hubspot://<alias>/companies/records.jsonl       companies
hubspot://<alias>/deals/records.jsonl           deals
hubspot://<alias>/tickets/records.jsonl         tickets
hubspot://<alias>/<custom-obj>/records.jsonl    custom objects (by object type ID)
```

Each record is the API result with `properties` flattened to top-level
(so `properties.email` becomes `email`).

## Auth

HubSpot's modern auth path is **Private Apps** with `access_token`:

```toml
credential_ref = "env:HUBSPOT_TOKEN"      # value: "pat-na1-xxx..."
```

Where to create: HubSpot → Settings → Integrations → **Private Apps** →
Create app → Scopes tab, grant CRM read scopes for the objects you want:

- `crm.objects.contacts.read`
- `crm.objects.companies.read`
- `crm.objects.deals.read`
- `crm.objects.tickets.read`
- `crm.schemas.contacts.read` (if you want schema.json)
- etc.

OAuth tokens (for marketplace apps) are not currently supported — Private
App access_token only.

## Connector config TOML

```toml
# ─── auth (required) ───
credential_ref = "env:HUBSPOT_TOKEN"

# ─── scope ───
# objects = ["contacts", "companies", "deals", "tickets"]   # default = the four above
# max_read_rows = 10000                                      # cap per object; default 1000

# ─── per-object field mapping ───
[[objects]]
match           = "/tickets/records.jsonl"
text_fields     = ["subject", "content"]
metadata_fields = ["hs_pipeline_stage", "hs_ticket_priority", "createdate"]
locator_fields  = ["id"]

[[objects]]
match           = "/deals/records.jsonl"
text_fields     = ["dealname", "notes_last_contacted"]
metadata_fields = ["dealstage", "amount", "closedate"]
locator_fields  = ["id"]

[[objects]]
match           = "/companies/records.jsonl"
text_fields     = ["name", "description", "domain"]
locator_fields  = ["id"]
```

## What each command does

| Command | Behaviour |
|---|---|
| `mfs ls /` | lists configured objects (`contacts`, `deals`, ...). |
| `mfs ls /<object>/` | `["records.jsonl"]`. |
| `mfs cat /<object>/records.jsonl` | **refused** (lazy). |
| `mfs cat .../records.jsonl --range A:B` | paged `basic_api.get_page(limit, after)`. |
| `mfs cat .../records.jsonl --locator '{"id":"123"}'` | `basic_api.get_by_id(123)`. |
| `mfs grep "PATTERN" .../records.jsonl` | linear scan (no pushdown). |
| `mfs search "QUERY"` | Milvus only. |

## Typical workflow

```bash
# 1. Create a Private App, grant scopes, copy access token.
export HUBSPOT_TOKEN="pat-na1-xxx..."

# 2. Register.
mfs add hubspot://acme --config hubspot-acme.toml

# 3. Search.
mfs search "renewal blocker security review" --connector-uri hubspot://acme
mfs cat hubspot://acme/tickets/records.jsonl --locator '{"id":"15873"}'

# 4. Refresh.
mfs add hubspot://acme --no-full
```

## Incremental sync

Per-object fingerprint = `total | max(hs_lastmodifieddate)`. Updates re-page
from the API; the connector breaks once it hits a known fingerprint.

## Gotchas

1. **Scopes matter** — a token without `crm.objects.deals.read` will fail
   on `/deals/`. The connector reports the API error; check the Private
   App's Scopes tab.
2. **`properties` flattening**: only the requested properties come back
   from the API. To get a property into your records, include it in the
   API call's `properties` list (the connector defaults to "all" properties
   — but for custom objects with hundreds of properties, narrow this for
   speed: `properties = ["subject", "content", "hs_pipeline_stage"]`
   future-feature, not yet exposed).
3. **Custom object types** are referenced by their object type ID
   (`"2-12345678"`) or singular fully-qualified name — check HubSpot's
   Settings → Objects → Custom objects for the right identifier.
4. **Rate limits**: HubSpot uses a quota model (per portal). For initial
   syncs on big portals, expect the connector to throttle naturally.
5. **No webhook live mode** — `mfs add --no-full` to refresh.
