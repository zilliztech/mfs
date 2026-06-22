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

Start with a dedicated read-only user and role. In Snowsight, open
*Admin → Users & Roles* to create the user/role, or run the equivalent SQL:

```sql
CREATE ROLE IF NOT EXISTS mfs_reader_role;
CREATE USER IF NOT EXISTS mfs_reader DEFAULT_ROLE = mfs_reader_role;
GRANT ROLE mfs_reader_role TO USER mfs_reader;
GRANT USAGE ON WAREHOUSE mfs_wh TO ROLE mfs_reader_role;
GRANT USAGE ON DATABASE PROD TO ROLE mfs_reader_role;
GRANT USAGE ON SCHEMA PROD.PUBLIC TO ROLE mfs_reader_role;
GRANT SELECT ON ALL TABLES IN SCHEMA PROD.PUBLIC TO ROLE mfs_reader_role;
GRANT SELECT ON FUTURE TABLES IN SCHEMA PROD.PUBLIC TO ROLE mfs_reader_role;
```

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
passphrase, set `private_key_passphrase_ref` too. Copy `rsa_key.p8` to a path the
server can read, keep it outside the repo, and restrict its permissions.

**Password** (`auth_mode = "password"`): `credential_ref` carries the password
(prefer an `env:`/`file:` ref). Snowflake is tightening password login — check your
account's MFA policy first.

**PAT** (`auth_mode = "pat"`): issue a Programmatic Access Token in the Snowflake
UI, attach a network policy covering your egress IPs, and put the token in
`credential_ref`. Rotation is just "issue a new PAT, replace the secret".

![Snowflake Users and roles page](https://github.com/user-attachments/assets/102fd1cf-3841-4606-918e-0cff54f356c2)

![Snowflake programmatic access tokens section](https://github.com/user-attachments/assets/7fae2256-cf98-4750-8236-0fca285ab69f)

![Snowflake new programmatic access token dialog](https://github.com/user-attachments/assets/9a2ba38a-8bed-4c9b-8c6b-1b6dcafd7909)

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

Save the file as `snowflake.toml`, then probe before the first index:

```bash
mfs connector probe snowflake://analytics --config ./snowflake.toml
mfs add snowflake://analytics --config ./snowflake.toml
```

## Sync and freshness

The connector uses table `row_count` as its change signal; deletions are caught by
`full_scan`. Warehouses may auto-resume on the first query, adding startup
latency.

## Search and browse

```bash
mfs search "billing event" snowflake://analytics/PROD/PUBLIC/tables/TICKETS/rows.jsonl
mfs search "EMAIL column" snowflake://analytics --kind schema_summary
mfs cat snowflake://analytics/PROD/PUBLIC/tables/TICKETS/rows.jsonl --locator '{"ID":12345}'
```

## Pitfalls

- Identifiers fold to uppercase — paths and locators must match the returned
  casing.
- Rows need `text_fields` to be searchable.
- The warehouse must be usable by the connector role; a valid key with no
  warehouse `USAGE` grant still fails at probe time.
- Password auth is subject to Snowflake's tightening login policies; prefer
  key-pair.
