# salesforce connector (`salesforce://`)

## What this is

Salesforce SObjects (Account / Contact / Opportunity / Case / Lead /
custom). Uses `simple-salesforce` (sync, wrapped in `asyncio.to_thread`)
to run SOQL against the org. Each configured SObject becomes a record
stream + a schema description.

**When MFS helps**: large Salesforce orgs with thousands of accounts /
opportunities — "any deals mentioning the security audit in the past
quarter?" — without writing SOQL by hand.

## URI shape

```
salesforce://<alias>/                              connector root
salesforce://<alias>/Account/records.jsonl        lazy SObject stream
salesforce://<alias>/Account/schema.json          field list (eager)
salesforce://<alias>/Case/records.jsonl
salesforce://<alias>/Opportunity/records.jsonl
salesforce://<alias>/<CustomObject>__c/records.jsonl
```

SObject names follow Salesforce's exact casing (`Account`, `MyCustom__c`).

## Auth — two paths

**A. Username + password + security token** (legacy but widely used):

```toml
username        = "service.mfs@acme.com"
password        = "..."
credential_ref  = "env:SF_SECURITY_TOKEN"     # the security token (separate from password)
```

Where to get the security token: Salesforce → personal Settings → "Reset
My Security Token" (sent to your email).

**B. instance_url + session_id** (when you've authenticated elsewhere and
have a live session):

```toml
instance_url = "https://acme.my.salesforce.com"
credential_ref = "env:SF_SESSION_ID"          # the active session id
```

Session IDs expire — they're for short-lived integrations or test setups,
not production.

For production, OAuth via Connected App is the right path; not yet
supported by the connector. Track that as a future enhancement.

## Connector config TOML

```toml
# ─── auth: pick ONE flavour ───
# A) username+password+security_token
username       = "service.mfs@acme.com"
password       = "..."                                # gets redacted on persist; use env for prod
credential_ref = "env:SF_SECURITY_TOKEN"

# B) instance_url + session_id (uncomment instead of A)
# instance_url   = "https://acme.my.salesforce.com"
# credential_ref = "env:SF_SESSION_ID"

# ─── optional ───
# domain   = "test"                # for sandboxes; default = "login" (prod)
# objects  = ["Account", "Contact", "Opportunity", "Case"]   # default = these four
# max_read_rows = 10000

# ─── per-object field mapping ───
[[objects]]
match           = "/Case/records.jsonl"
text_fields     = ["Subject", "Description"]
metadata_fields = ["Status", "Priority", "CreatedDate"]
locator_fields  = ["Id"]

[[objects]]
match           = "/Opportunity/records.jsonl"
text_fields     = ["Name", "Description"]
metadata_fields = ["StageName", "Amount", "CloseDate"]
locator_fields  = ["Id"]
```

## What each command does

| Command | Behaviour |
|---|---|
| `mfs ls /` | configured SObjects. |
| `mfs ls /<obj>/` | `["records.jsonl", "schema.json"]`. |
| `mfs cat /<obj>/records.jsonl` | **refused** (lazy). |
| `mfs cat .../records.jsonl --range A:B` | SOQL `SELECT FIELDS(ALL) FROM <obj> LIMIT (B-A) OFFSET A`. |
| `mfs cat .../records.jsonl --locator '{"Id":"003..."}'` | SOQL `WHERE Id = '003...'`. |
| `mfs cat /<obj>/schema.json` | `sf.<Obj>.describe()` — full field metadata. |
| `mfs grep "PATTERN" .../records.jsonl` | linear scan; no SOSL pushdown (a future option). |
| `mfs search "QUERY"` | Milvus only. |

## Typical workflow

```bash
# 1. Reset and save the security token (one-time, account-level).
#    Salesforce → Settings → "Reset My Security Token" — token arrives via email.
export SF_SECURITY_TOKEN="ABcDeFGhIjKlMnOpQrStUvWxYz"

# 2. Register.
cat > sf-prod.toml <<'EOF'
username = "service.mfs@acme.com"
password = "..."     # store via secret manager for real prod
credential_ref = "env:SF_SECURITY_TOKEN"
objects  = ["Account", "Contact", "Opportunity", "Case"]
EOF
mfs add salesforce://prod --config sf-prod.toml

# 3. Search and locate.
mfs search "security audit blocker" --connector-uri salesforce://prod
mfs cat salesforce://prod/Case/records.jsonl --locator '{"Id":"5003x..."}'

# 4. Refresh.
mfs add salesforce://prod --no-full
```

## Incremental sync

Per-object fingerprint = `count | max(LastModifiedDate)`. SOQL pushdown is
`SELECT ... WHERE LastModifiedDate > <last_max>` ordered ascending.

## Gotchas

1. **Security token != password.** The `credential_ref` for path-A auth is
   the **security token** (received by email after `Reset My Security
   Token`), NOT the user's password. The password goes in the `password`
   config field (which is redacted on persistence — for production, embed
   the password in a sibling secret too via a future field).
2. **Sandbox vs production**: set `domain = "test"` for `*.sandbox.my.salesforce.com`
   logins.
3. **`FIELDS(ALL)` SOQL** is restricted to 200 rows per call in Salesforce
   API ≥ v52. The connector handles paging, but custom SObjects with
   hundreds of fields can be slow on initial sync.
4. **Custom objects** end with `__c`. Use the exact name including the
   suffix in `objects = [...]`.
5. **No OAuth path today** — Connected App OAuth is a planned enhancement.
6. **Permissions**: the user running the connector needs "Read" on the
   listed SObjects. Field-level security restrictions are honoured — fields
   the user can't see come back as null and embed as empty.
