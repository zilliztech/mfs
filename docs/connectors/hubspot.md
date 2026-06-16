# HubSpot (`hubspot`)

The `hubspot` connector indexes HubSpot CRM objects — contacts, companies, deals,
tickets — as searchable records.

## How MFS sees it

```text
hubspot://acme/
├── contacts/records.jsonl     record_collection
├── companies/records.jsonl
├── deals/records.jsonl
└── tickets/records.jsonl
```

HubSpot returns objects as `{id, properties:{…}}`; the connector flattens
`properties` to top-level fields and fetches each object's default property set.
Built-in presets cover the common objects (`hubspot.contacts`,
`hubspot.companies`, `hubspot.deals`, `hubspot.tickets`), so basic indexing works
without config; add `[[objects]]` to index non-default properties.

## Credentials

HubSpot uses a **Private App access token**.

1. <https://app.hubspot.com> → *Settings → Integrations → Private Apps* →
   *Create a private app* (if shown the *Legacy Apps* page, create a legacy app
   for one account).
2. On the *Scopes* tab, enable the read scopes you need:
   `crm.objects.contacts.read`, `crm.objects.companies.read`,
   `crm.objects.deals.read`, and `tickets` (Service Hub only).
3. *Create app* → copy the access token (`pat-na1-…` for NA1, `pat-eu1-…` for
   EU). **It's shown only once.**

## Configuration

```toml
access_token = "env:HUBSPOT_ACCESS_TOKEN"
object_types = ["contacts", "companies", "deals", "tickets"]
max_read_rows = 50000

[[objects]]
match = "/contacts"
text_fields = ["firstname", "lastname", "email", "jobtitle"]
locator_fields = ["id"]
```

If `object_types` is omitted, the connector probes the common objects and silently
drops the ones the portal rejects (a 403 on Service Hub tickets, say).

## Sync and freshness

The connector uses each object's `hs_lastmodifieddate` as its cursor for
incremental re-sync; deletions are caught by `full_scan`.

## Search and browse

```bash
mfs add hubspot://acme --config ./hubspot.toml

mfs search "customer health" hubspot://acme/contacts/records.jsonl
mfs cat hubspot://acme/contacts/records.jsonl --locator '{"id":"12345"}'
```

## Pitfalls

- Presets cover only each object's **default** property set; add `[[objects]]` to
  index custom properties.
- Engagement records (calls, notes, emails) are not included.
- Properties are flattened from the `properties` envelope to top-level fields.
