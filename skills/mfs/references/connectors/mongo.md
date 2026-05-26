# mongo connector (`mongo://`)

MongoDB collections as `/<collection>/documents.jsonl` + `schema.json` (one
database per connector). `documents.jsonl` is **lazy** (not materialized);
`schema.json` is sampled from the first document.

object_kind = `record_collection`. **search** runs over `record_aggregate`/
`row_text` chunks built per document from configured `text_fields`; each hit's
`locator` = `{_id: ...}` (the Mongo document id, stringified), `lines` is null →
reopen with `mfs cat <source> --locator '{"_id":"..."}'`.

Config in `[[objects]]`: `text_fields` (→ chunk content), `locator_fields`
(default `_id`), `metadata_fields`. Without `text_fields` the collection isn't searchable.
Nested fields use dotted paths; array fields use `field[].sub`.
