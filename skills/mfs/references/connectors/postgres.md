# postgres / mysql connectors (`postgres://`, `mysql://`)

Relational tables as a virtual tree. postgres: `/<schema>/<table>/rows.jsonl` +
`schema.json`. mysql: `/<table>/rows.jsonl` + `schema.json` (one database).

`rows.jsonl` is **lazy** — not materialized. `cat` of the full object is refused;
use `mfs head -n N`, `mfs cat --range A:B`, or `mfs cat --locator '{...}'`.

**search** runs over `row_text` chunks built per row from configured
`text_fields`. Each hit's `locator` = `{schema, table, pk:{...}}` (mysql: `{pk}`),
`lines` is null → reopen the exact row with `mfs cat <source> --locator '{"pk":{"id":12}}'`.

**grep** pushes down to SQL `ILIKE`/`LIKE` (literal-exact). `head -n N` → `SELECT ... LIMIT N`.

Config (no preset — business fields must be declared) in connector `[[objects]]`:
`text_fields` (→ chunk content), `metadata_fields` (→ filters), `locator_fields`
(→ pk), `chunk_strategy="per_row"`. Without `text_fields`, the table is not searchable.
