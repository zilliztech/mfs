# hubspot connector — ingest

URI: `hubspot://<alias>`.

## How to obtain credentials

HubSpot uses a **Private App access token**:

1. <https://app.hubspot.com> → Settings (gear icon) → **Integrations →
   Private Apps** → **Create a private app**.
2. Name + description.
3. **Scopes** tab → enable read scopes:
   - `crm.objects.contacts.read`
   - `crm.objects.companies.read`
   - `crm.objects.deals.read`
   - `tickets` (Service Hub only) — read tickets
4. **Create app** → on the next screen, copy the token (`pat-na1-…` for
   NA1 region, `pat-eu1-…` for EU). Token is shown ONCE.

## Required toml fields

| key | what |
|---|---|
| `access_token` | the `pat-…` token (`env:HUBSPOT_ACCESS_TOKEN` recommended) |

## Optional

| key | default | meaning |
|---|---|---|
| `object_types` | `probe-and-skip` | which object kinds to index; default tries `contacts`, `companies`, `deals`, `tickets` and silently drops the ones the portal returns 403 on (e.g. tickets without Service Hub) |

To force specific objects:
```toml
object_types = ["contacts", "deals"]
```

To include custom objects:
```toml
object_types = ["contacts", "companies", "deals", "p_customer_health"]
```

(Custom HubSpot objects have an internal name like `p_<name>` —
visible in the developer settings.)

## URI tree

```
hubspot://<alias>/
└── objects/
    ├── contacts/records.jsonl
    ├── companies/records.jsonl
    ├── deals/records.jsonl
    └── tickets/records.jsonl       ← only if Service Hub + scope
```

## env: example

```toml
access_token = "env:HUBSPOT_ACCESS_TOKEN"
object_types = ["contacts", "companies", "deals", "tickets"]
```

```bash
export HUBSPOT_ACCESS_TOKEN=pat-na1-...
mfs add hubspot://acme --config /tmp/mfs-hubspot.toml
```

## Pitfalls

- **Tier-gated objects**: `tickets` requires Service Hub Starter or
  higher. `quotes` requires Sales Hub Professional. The default
  `probe-and-skip` mode auto-drops 403s.
- **`properties` envelope**: HubSpot returns each record as
  `{id, properties: {...}}`. The connector flattens `properties.*` to
  top-level so `text_fields=["firstname", "lastname", "email"]` works
  directly.
- **Engagement-related properties**: most useful fields are on the
  associated activity/engagement records, NOT the contact itself. The
  connector indexes the primary object only; engagements are out of
  scope for this version.
- **Rate limit**: 100 req/10sec, 250k/day. Tens of thousands of
  contacts ingest fine; millions hit daily caps.
