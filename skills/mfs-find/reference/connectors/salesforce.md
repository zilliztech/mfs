# salesforce connector — search & browse

## URI tree

```
salesforce://<alias>/
└── objects/
    ├── Account/records.jsonl
    ├── Contact/records.jsonl
    ├── Opportunity/records.jsonl
    ├── Case/records.jsonl
    └── <Custom_Object__c>/records.jsonl
```

One `records.jsonl` per Salesforce sObject configured at ingest.

## Record shape

Standard sObject fields at top level:

```json
{"Id": "001AB...",
 "Name": "Acme Corp",
 "Description": "...",
 "Industry": "Technology",
 "AnnualRevenue": 5000000,
 "LastModifiedDate": "2026-06-01T..."}
```

Custom fields end with `__c`.

## Chunk kind

`row_text`. Content depends on configured `text_fields` — typically
`Description`, `Name`, `Notes__c`, etc.

## Locator

```bash
mfs cat salesforce://<alias>/objects/Account/records.jsonl \
  --locator '{"Id": "001AB..."}'
```

Salesforce IDs are 15 or 18 chars (the 18-char form is case-insensitive).

## Search strategy

| Intent | Use |
|---|---|
| Find accounts about X | `mfs search "X" salesforce://<alias>/objects/Account/records.jsonl` |
| Find opportunities by topic | `mfs search "X" salesforce://<alias>/objects/Opportunity/records.jsonl` |
| Cross-object | `mfs search "X" salesforce://<alias>` |

## Pitfalls

- **Field-level security trims content**: the API user's profile
  determines which fields come back. If a `Description` is missing
  for every record but visible in the UI, FLS is blocking it.
- **Custom objects: must list them**: standard objects (`Account`,
  `Contact`, …) are auto-included; `Custom_Object__c` must be in the
  toml's `objects` array.
- **Daily API limit**: tenants have daily caps. Big orgs hit them on
  first full sync.
- **Sandbox vs prod confusion**: `Id`s differ between environments.
