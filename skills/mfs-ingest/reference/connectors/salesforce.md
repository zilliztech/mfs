# salesforce connector — ingest

URI: `salesforce://<alias>`.

## How to obtain credentials

Salesforce uses **username + password + security token** (SOAP login)
OR **OAuth** (more setup, not yet supported by this connector).

1. **Username + Password**: your normal Salesforce login.
2. **Security Token**: <https://help.salesforce.com> → Settings → My
   Personal Information → Reset My Security Token. A new token is
   emailed to you. Required when API access is from outside the org's
   trusted IP range.
3. **Instance URL**: visible after login in the URL bar (e.g.
   `https://acme.my.salesforce.com`).
4. **Domain**: `login` for production, `test` for sandbox.

## Required toml fields

| key | what |
|---|---|
| `instance_url` | full URL (`https://acme.my.salesforce.com`) |
| `username` | login email |
| `password` | (`env:SF_PASSWORD` recommended) |
| `security_token` | (`env:SF_SECURITY_TOKEN` recommended) |

## Optional

| key | default | meaning |
|---|---|---|
| `domain` | `login` | `login` (prod) / `test` (sandbox) |
| `objects` | _auto-detect_ | sObjects to index (e.g. `["Account", "Contact", "Opportunity"]`) |

## URI tree

```
salesforce://<alias>/
└── objects/
    ├── Account/records.jsonl
    ├── Contact/records.jsonl
    ├── Opportunity/records.jsonl
    └── ...
```

## env: example

```toml
instance_url = "https://acme.my.salesforce.com"
username = "alice@acme.com"
password = "env:SF_PASSWORD"
security_token = "env:SF_SECURITY_TOKEN"
domain = "login"
objects = ["Account", "Contact", "Opportunity", "Case"]
```

```bash
export SF_PASSWORD=...
export SF_SECURITY_TOKEN=...
mfs add salesforce://acme --config /tmp/mfs-sf.toml
```

## Pitfalls

- **Security token mandatory** when API call comes from an untrusted
  IP. If the user gets `INVALID_LOGIN`, it's usually the security token
  missing.
- **API limits**: Salesforce Lightning has daily API call limits. Big
  orgs may hit the cap on first sync. Schedule re-syncs off-peak.
- **Custom objects**: anything ending in `__c` is a custom object.
  Include in `objects` explicitly.
- **Field-level security**: the user's profile limits which fields the
  API returns. If `Description` comes back empty for everyone, check
  field-level security on that profile.
- **Sandbox domain**: domain mismatch causes `INVALID_OPERATION`.
  `login` ↔ `test` is the most common typo.
