# snowflake connector (`snowflake://`)

## What this is

Snowflake is a cloud data warehouse. The MFS snowflake connector exposes one
Snowflake account as a filesystem tree — databases / schemas / tables become
directories, each table's rows become a lazy file, each table's columns become
a schema file. The connector queries Snowflake via the official
`snowflake-connector-python` driver (sync, wrapped in `asyncio.to_thread`).

**When MFS helps**: you have many tables (dozens to hundreds) and want to
search across their content semantically — "which table mentions SSO bounce" /
"any rows about a payment outage" — without having to know table names or
write SQL across them.

**When SQL beats MFS**: a single known table, structured analytics, joins,
aggregations. MFS doesn't replace your warehouse — it makes the *content* of
the warehouse findable.

**Cost note**: MFS only hits Snowflake during sync (`mfs add` / re-sync) and
when you call `cat --locator` / `cat --range` / `head` (those execute SQL).
`search` runs against MFS's local Milvus index, never touches Snowflake.

## URI shape

A registered connector has an opaque alias like `snowflake://prod-analytics`.
Underneath it the connector enumerates this layout (from
`information_schema`):

```
/                                              (root: list of databases)
/ANALYTICS/                                     database
/ANALYTICS/PUBLIC/                              schema
/ANALYTICS/PUBLIC/tables/                       folder
/ANALYTICS/PUBLIC/tables/TICKETS/               table folder
/ANALYTICS/PUBLIC/tables/TICKETS/rows.jsonl     table rows (LAZY — never materialized)
/ANALYTICS/PUBLIC/tables/TICKETS/schema.json    column list (small, eager)
```

Single database per connector via `database = "..."`, or multiple via
`databases = ["A", "B"]`. Column / table / schema names are returned
**uppercase** by Snowflake (unquoted identifiers are folded to upper).

## Auth — key-pair (RSA JWT) ONLY

Password and PAT auth are **not supported**. Reasons:

- Snowflake is phasing out single-factor password login.
- PATs require a network policy on the bound user (operationally painful in
  k8s / serverless where egress IPs drift).
- Key-pair is Snowflake's recommended path for programmatic access and has no
  network-policy requirement.

### One-time setup (per Snowflake user)

```bash
# 1) Generate an unencrypted 2048-bit RSA PKCS#8 keypair on the MFS server host.
mkdir -p /var/run/secrets/snowflake && cd $_
openssl genrsa 2048 2>/dev/null \
  | openssl pkcs8 -topk8 -inform PEM -outform PEM -nocrypt -out key.p8
openssl rsa -in key.p8 -pubout -out key.pub 2>/dev/null
chmod 600 key.p8

# 2) Print the public-key body (no BEGIN/END lines, no newlines) — paste into Snowsight:
grep -v 'PUBLIC KEY' key.pub | tr -d '\n'; echo
```

In Snowsight, as `ACCOUNTADMIN`:

```sql
ALTER USER <YOUR_USER> SET RSA_PUBLIC_KEY = '<paste-pubkey-body-here>';
DESC USER <YOUR_USER>;   -- confirm RSA_PUBLIC_KEY is non-NULL
```

### Encrypted private keys

The default config above is **unencrypted**, protected by file permissions
(`chmod 600`) — same hygiene as an SSH private key. If you want an encrypted
PEM, generate with `openssl pkcs8 -topk8 -v2 aes-256-cbc` and provide a
sibling `private_key_passphrase_ref` config field that resolves the same way
as `credential_ref` (`env:` / `file:`).

## Connector config TOML

```toml
# ─── connection (required) ───
account = "PQELFFW-OE99512"          # Snowsight → account menu → "Account identifier"
user    = "ZC277584121"              # the user whose RSA_PUBLIC_KEY you set above
warehouse = "ANALYTICS_WH"           # must exist; queries need a running warehouse
database  = "ANALYTICS"              # scope to one DB; or use databases=[...] for many
role      = "MFS_READONLY"           # strongly recommended: a dedicated narrow-scope role

credential_ref = "file:/var/run/secrets/snowflake/key.p8"   # PEM private key

# ─── optional ───
# schema = "PUBLIC"                              # default schema; does NOT scope the scan
# databases = ["ANALYTICS", "RAW"]               # multi-DB; takes precedence over `database`
# max_read_rows = 200000                         # LIMIT on each rows.jsonl read; default 100000
# cursor_column = "UPDATED_AT"                   # incremental cursor; auto-detected from
                                                 # ["updated_at", "modified_at", "last_modified",
                                                 #  "updated", "modified", "mtime"] (case-insensitive)
# private_key_passphrase_ref = "env:SF_KEY_PW"   # only if the PEM is encrypted

# ─── per-table field mapping (required if you want rows searchable) ───
[[objects]]
match           = "/ANALYTICS/PUBLIC/tables/TICKETS/rows.jsonl"   # or "*rows.jsonl" for all
text_fields     = ["SUBJECT", "BODY"]    # these columns -> embedding content (UPPERCASE)
locator_fields  = ["ID"]                 # primary key for `cat --locator`
metadata_fields = ["STATUS", "PRIORITY"] # carried as filterable metadata
# indexable = true               # default; set false to keep ls/cat but NOT index (PII tables)
# chunk_max = 1_000_000          # safety cap on chunks per table; default 1M
```

### Field-mapping rules (the only knobs left, post-v0.4 simplification)

- **`text_fields` is mandatory if you want the rows to be searchable.** Without
  it, the connector still enumerates the table and `ls/cat/head` work, but
  search finds nothing for that table.
- Names must be **UPPERCASE** to match what Snowflake returns. Lowercase silently
  produces 0 chunks per row.
- All three field lists support JSONPath-lite: `a.b`, `a[*].b`, `a[2].b`,
  `a[0:5].b`. For Snowflake this is only useful with `VARIANT` / `OBJECT` /
  `ARRAY` columns.
- Multiple `[[objects]]` blocks are matched in declaration order; first match
  wins. Put specific table patterns before catch-alls.
- Chunking strategy is decided by the engine, not the user: tables go per-row.

## What each command does on this connector

| Command | Behaviour against Snowflake |
|---|---|
| `mfs ls /` | lists databases (the configured `database` / `databases`). |
| `mfs ls /<db>/` | lists schemas via `information_schema.schemata`. |
| `mfs ls /<db>/<schema>/tables/` | lists tables via `information_schema.tables`. |
| `mfs tree /<db>` | recursive list, depth-bounded by the path. |
| `mfs cat /<db>/<schema>/tables/<tbl>/rows.jsonl` | **refused** — `rows.jsonl` is lazy. Use `head`, `--range`, or `--locator`. |
| `mfs cat .../rows.jsonl --range A:B` | executes `SELECT * FROM <tbl> LIMIT (B-A) OFFSET A`. |
| `mfs cat .../rows.jsonl --locator '{"ID":1}'` | executes `SELECT * FROM <tbl> WHERE "ID"=1`. Keys from `locator_fields`. |
| `mfs head -n N .../rows.jsonl` | `SELECT * FROM <tbl> LIMIT N`. Hits a `head_cache` artifact first for repeated runs. |
| `mfs cat .../schema.json` | one-shot `information_schema.columns` query for the column list. |
| `mfs grep PATTERN .../rows.jsonl --field SUBJECT` | pushed down to `SELECT ... WHERE "SUBJECT" ILIKE '%PATTERN%'`. Literal only (no regex). Falls back to row scan if no `--field`. |
| `mfs search "QUERY"` | runs against Milvus only — never queries Snowflake. Returns `row_text` chunks (per-row content from `text_fields`) and `schema_summary` chunks (LLM description of each table's columns, only when `summary.enabled = true` in server.toml). |
| `mfs search ... --connector-uri snowflake://prod-analytics` | scopes search to this connector via Milvus partition key. |
| `mfs export <path>` | not supported for `rows.jsonl` (lazy). Use `head -n N > file.jsonl`. |

## Typical workflow

```bash
# 1. Provision once on the Snowflake side (Snowsight, as ACCOUNTADMIN):
#    - create a warehouse (or use an existing one)
#    - create a role with USAGE on warehouse + USAGE/SELECT on the target databases
#    - ALTER USER ... SET RSA_PUBLIC_KEY = '...'  (as shown in the auth section)

# 2. Drop the private key on the MFS server host at /var/run/secrets/snowflake/key.p8

# 3. Write snowflake-prod.toml (see config block above), then:
mfs add snowflake://prod-analytics --config snowflake-prod.toml

# 4. Wait for the initial sync to finish — `mfs status` shows job progress.
mfs status

# 5. Search and locate:
mfs search "single sign-on bounce" --connector-uri snowflake://prod-analytics --top-k 5
# A hit looks like:
#   source: snowflake://prod-analytics/ANALYTICS/PUBLIC/tables/TICKETS/rows.jsonl
#   locator: {"ID": 9001}
#   content: 'SUBJECT: SSO login fails\n\nBODY: Users bounced back to the IdP...'

# 6. Reopen the exact row:
mfs cat snowflake://prod-analytics/ANALYTICS/PUBLIC/tables/TICKETS/rows.jsonl \
       --locator '{"ID":9001}'

# 7. Modify on Snowflake side, refresh MFS incrementally (cursor_column drives this):
mfs add snowflake://prod-analytics --no-full       # re-sync changed tables only
```

## Incremental sync

Each table's fingerprint is `count(*) | max(cursor_column)` (or just `count(*)`
if no cursor column matched). On re-sync, fingerprint mismatch triggers a
re-index of that one table:

- INSERTs change `count` → detected.
- UPDATEs change `max(cursor_column)` → detected **only if you have a cursor
  column that actually updates**. If your source table has no auto-updated
  timestamp, in-place edits will be missed. Add a `LAST_MODIFIED` column with
  `DEFAULT CURRENT_TIMESTAMP` and update it on each row mutation.
- DELETEs change `count` → detected on next sync. Removed rows are purged
  from Milvus.

DDL drift (added/dropped/typed column) is captured by the separate
`schema.json` fingerprint (`schema:col1:TYPE1;col2:TYPE2;…`) → re-summarizes.

## Gotchas

1. **Column names must be UPPERCASE** in `text_fields` / `locator_fields` /
   `metadata_fields`. Snowflake folds unquoted identifiers to upper. Wrong
   case = 0 chunks indexed silently.
2. **A warehouse is required and must be unsuspended** when queries run.
   `AUTO_RESUME=TRUE` is the standard production setting; `AUTO_SUSPEND=60`
   (seconds) keeps idle cost negligible.
3. **`text_fields` not configured = no rows searchable.** `schema_summary`
   may still be produced (one chunk per table) when `summary.enabled` is on,
   so you can search "which tables exist", but not row content.
4. **`max_read_rows` caps the scan.** A table with more rows is marked
   `search_status = partial`. Check `mfs connector status` to spot this.
5. **`indexable = false` doesn't save Snowflake compute** — it only skips
   Milvus indexing. `ls`/`cat`/`head` still execute SQL on the table. If you
   want to fully exclude a table from MFS, omit its `[[objects]]` match and
   set a narrower `database` / `databases` scope.
6. **Network policy is NOT required for key-pair auth** (unlike PAT). If you
   previously set one for testing, you can unset it.
