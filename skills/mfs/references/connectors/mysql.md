# mysql connector (`mysql://`)

MySQL/MariaDB tables as a virtual tree: `/<table>/rows.jsonl` + `schema.json`
(one database per connector). Identical model to **postgres** — see
[postgres.md](postgres.md) for the full layout, locator shape, and config.

`rows.jsonl` is lazy; reopen a hit with `mfs cat <source> --locator '{"pk":{"id":12}}'`.
`grep` pushes down to SQL `LIKE`. Declare `text_fields` / `locator_fields` in
`[[objects]]` or the table is not searchable.
