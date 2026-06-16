# Snowflake (`snowflake`)

The `snowflake` connector indexes Snowflake table rows as searchable records, with
a schema summary per table.

## How MFS sees it

Snowflake folds unquoted identifiers to uppercase, so paths and locators are
uppercase too:

```text
snowflake://analytics/
└── PROD/
    └── PUBLIC/
        └── tables/
            └── TICKETS/
                ├── rows.jsonl     table_rows    → one searchable chunk per row
                └── schema.json    table_schema  → searchable column summary
```

Rows are chunked per-row and need `[[objects]].text_fields` to become searchable.

## Credentials

Three auth modes, selected with `auth_mode`. **Key-pair is the default and the
recommended production path.**

**Key-pair** (`auth_mode = "key-pair"`):

```bash
openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out rsa_key.p8 -nocrypt
openssl rsa -in rsa_key.p8 -pubout -out rsa_key.pub
```

Register the public key on the user, then point `credential_ref` at the private
key:

```sql
ALTER USER mfs_reader SET RSA_PUBLIC_KEY='<rsa_key.pub minus header/footer>';
```

`credential_ref` must resolve to a PEM PKCS#8 RSA private key. If it has a
passphrase, set `private_key_passphrase_ref` too.

**Password** (`auth_mode = "password"`): `credential_ref` carries the password
(prefer an `env:`/`file:` ref). Snowflake is tightening password login — check your
account's MFA policy first.

**PAT** (`auth_mode = "pat"`): issue a Programmatic Access Token in the Snowflake
UI, attach a network policy covering your egress IPs, and put the token in
`credential_ref`. Rotation is just "issue a new PAT, replace the secret".

Use a read-only role with `USAGE` on the warehouse/database/schemas and `SELECT`
on the in-scope tables.

## Configuration

```toml
account = "abcdefg-xy12345"
user = "mfs_reader"
warehouse = "mfs_wh"
role = "mfs_reader_role"
database = "PROD"               # or databases = ["PROD", "ANALYTICS"]
auth_mode = "key-pair"
credential_ref = "file:/etc/mfs/snowflake/rsa_key.p8"

[[objects]]
match = "/PROD/PUBLIC/tables/TICKETS"
text_fields = ["TITLE", "DESCRIPTION"]
locator_fields = ["ID"]
```

## Sync and freshness

The connector uses table `row_count` as its change signal; deletions are caught by
`full_scan`. Warehouses may auto-resume on the first query, adding startup
latency.

## Search and browse

```bash
mfs add snowflake://analytics --config ./snowflake.toml

mfs search "billing event" snowflake://analytics/PROD/PUBLIC/tables/TICKETS/rows.jsonl
mfs search "EMAIL column" snowflake://analytics --kind schema_summary
mfs cat snowflake://analytics/PROD/PUBLIC/tables/TICKETS/rows.jsonl --locator '{"ID":12345}'
```

## Pitfalls

- Identifiers fold to uppercase — paths and locators must match the returned
  casing.
- Rows need `text_fields` to be searchable.
- Password auth is subject to Snowflake's tightening login policies; prefer
  key-pair.
