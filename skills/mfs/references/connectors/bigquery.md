# bigquery connector (`bigquery://`)

BigQuery tables as `/<dataset>/tables/<table>/rows.jsonl` + `schema.json`.
`rows.jsonl` is **lazy** — `cat` of the whole object is refused; use `head -n N`
(→ `SELECT ... LIMIT N`) or `cat --locator`.

object_kind = `table_rows`. **search** runs over `row_text` chunks built per row
from configured `text_fields`; `locator` is flat, keyed by `locator_fields` (e.g. `{"id":12}`) (declare
`locator_fields`), `lines` null. Same structured model as **postgres** — see
[postgres.md](postgres.md) for config.

Auth: ADC / service-account JSON via `GOOGLE_APPLICATION_CREDENTIALS`. Big tables
cost money to scan — prefer `index_filter` and a bounded `chunk_max`.
