# hubspot connector — search & browse

## URI tree

```
hubspot://<alias>/
└── objects/
    ├── contacts/records.jsonl
    ├── companies/records.jsonl
    ├── deals/records.jsonl
    ├── tickets/records.jsonl    ← only if Service Hub + scope
    └── <p_custom>/records.jsonl ← custom objects
```

## Record shape

The connector flattens HubSpot's `{id, properties: {...}}` envelope so
properties appear at the top level:

```json
{"id": "12345",
 "firstname": "Alice",
 "lastname": "Wang",
 "email": "alice@acme.com",
 "company": "Acme Corp",
 "lifecyclestage": "customer",
 "lastmodifieddate": "2026-06-01T..."}
```

## Chunk kind

`row_text`. Typical `text_fields` per object:
- contacts: `firstname + lastname + email + jobtitle + notes`
- companies: `name + domain + description + industry`
- deals: `dealname + description + dealstage`
- tickets: `subject + content + status`

## Locator

```bash
mfs cat hubspot://<alias>/objects/contacts/records.jsonl \
  --locator '{"id": "12345"}'
```

## Search strategy

| Intent | Use |
|---|---|
| Find contacts about X | `mfs search "X" hubspot://<alias>/objects/contacts/records.jsonl` |
| Find deals by topic | `mfs search "X" hubspot://<alias>/objects/deals/records.jsonl` |
| Cross-object search | `mfs search "X" hubspot://<alias>` |

## Pitfalls

- **Tier-gated objects**: free / Starter tenants don't have `tickets`
  or `quotes`. Default `probe-and-skip` auto-drops them with no
  error.
- **Property names lowercase**: HubSpot internal names are lowercase
  (`firstname`, not `FirstName`). Custom properties keep their
  configured internal name.
- **Engagement records (calls, emails, notes) NOT indexed**: only
  primary objects. To search "what was discussed in this account's
  emails" you'd need a separate ingestion of engagement objects (not
  shipped in v1).
- **Daily rate limit**: 250k req/day. Big tenants ingest in chunks.
