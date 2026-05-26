# salesforce connector (`salesforce://`)

Salesforce SObjects as `/<object>/records.jsonl` + `schema.json` for each
configured object (Account / Contact / Opportunity / Case / Lead / ...). Records
are pulled via SOQL (`SELECT <all fields> FROM <object>`); lazy.

object_kind = `record_collection`. **search** over `record_aggregate` chunks from
configured `text_fields` (e.g. `Name`, `Description`, `Subject`); `locator` =
`{Id: "003..."}`, `lines` null → `mfs cat <source> --locator '{"Id":"003..."}'`.

Config: list `objects` to expose; per object set `text_fields`, `locator_fields`
`["Id"]`, `metadata_fields`. Auth: `username`+`password`+`security_token`, or
`instance_url`+`session_id`.
