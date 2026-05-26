# snowflake connector (`snowflake://`)

Snowflake tables as `/<database>/<schema>/tables/<table>/rows.jsonl` + `schema.json`.
`rows.jsonl` is **lazy**; `head -n N` pushes down to `SELECT ... LIMIT N`.

object_kind = `table_rows`. **search** over `row_text` chunks from configured
`text_fields`; `locator` = `{database, schema, table, pk:{...}}`, `lines` null →
reopen with `mfs cat <source> --locator '{...}'`. Structured model identical to
**postgres** ([postgres.md](postgres.md)).

Auth via connector config (`account`/`user`/`password`/`role`/`warehouse`) or an
`authenticator`. Declare `text_fields`/`locator_fields` in `[[objects]]`.
