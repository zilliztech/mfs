# snowflake connector — ingest

URI: `snowflake://<alias>`.

## How to obtain credentials

Snowflake requires **key-pair JWT authentication** (passwords are no
longer supported by the connector). Generate an RSA key:

```bash
openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out rsa_key.p8 -nocrypt
openssl rsa -in rsa_key.p8 -pubout -out rsa_key.pub
```

Register the public key on a Snowflake user:
```sql
ALTER USER mfs_reader SET RSA_PUBLIC_KEY='<contents of rsa_key.pub minus header/footer>';
```

The private key file path goes into the toml.

## Required scopes / role

Create a narrow-scope role:
```sql
CREATE ROLE mfs_reader_role;
GRANT USAGE ON WAREHOUSE mfs_wh TO ROLE mfs_reader_role;
GRANT USAGE ON DATABASE prod TO ROLE mfs_reader_role;
GRANT USAGE ON ALL SCHEMAS IN DATABASE prod TO ROLE mfs_reader_role;
GRANT SELECT ON ALL TABLES IN DATABASE prod TO ROLE mfs_reader_role;
GRANT ROLE mfs_reader_role TO USER mfs_reader;
```

The warehouse must be **resumable** — Snowflake auto-suspends idle
warehouses, the connector will fail if the role can't resume.

## Required toml fields

| key | what |
|---|---|
| `account` | account identifier (e.g. `ABCDEFG-XY12345`); shown in the URL of your Snowflake console |
| `user` | username |
| `warehouse` | warehouse name |
| `credential_ref` | path to the PKCS#8 PEM key, as `file:/abs/path/to/rsa_key.p8` |

## Optional

| key | meaning |
|---|---|
| `role` | role name (recommend `mfs_reader_role`) |
| `database` | one DB |
| `databases` | multi-DB list (alternative to `database`) |
| `private_key_passphrase_ref` | env:VAR or file:/path; required if the key has a passphrase |
| `max_read_rows` | per-table cap |

## `[[objects]]` blocks

```toml
[[objects]]
match = "PROD.PUBLIC.TICKETS"            # Snowflake folds to UPPERCASE
text_fields = ["TITLE", "DESCRIPTION"]   # column names UPPERCASE too
locator_fields = ["ID"]
```

## env: + file: example

```toml
account = "abcdefg-xy12345"
user = "mfs_reader"
warehouse = "mfs_wh"
role = "mfs_reader_role"
database = "prod"
credential_ref = "file:/etc/mfs/snowflake/rsa_key.p8"
# only if your key has a passphrase:
# private_key_passphrase_ref = "env:SNOWFLAKE_KEY_PASSPHRASE"

[[objects]]
match = "PROD.PUBLIC.TICKETS"
text_fields = ["TITLE", "DESCRIPTION"]
locator_fields = ["ID"]
```

## Pitfalls

- **Identifier case-folding**: Snowflake stores unquoted identifiers as
  uppercase. If you typed `tickets` in `match`, the connector folds it.
  If your table is actually `"tickets"` (quoted-lowercase), MFS won't
  find it.
- **Warehouse auto-suspend → first query slow**: the connector waits
  for the warehouse to resume, can take 10-30s.
- **`credential_ref` resolution**: must be `file:/abs/path` or
  `env:VAR` containing the PEM contents. A bare path doesn't get
  resolved.
