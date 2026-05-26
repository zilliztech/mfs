# notion connector (`notion://`)

Two kinds of object:
- **pages** → `/pages/<id>.md` — page block tree rendered to markdown, object_kind
  `document`. `cat` returns the markdown; search hits carry `lines [start,end]` →
  `mfs cat notion://<alias>/pages/<id>.md --range a:b`.
- **databases** → `/databases/<id>/records.jsonl` + `schema.json`, object_kind
  `record_collection`. Each row is flattened from Notion properties (title/select/
  multi_select/people/date/number/...). `locator` = `{id: "<page-id>"}`, `lines`
  null → `mfs cat <source> --locator '{"id":"..."}'`.

Config for databases: `text_fields` (the title + long-text properties),
`locator_fields` `["id"]`. Auth: integration token (`token`); the integration must
be shared into the pages/databases you want indexed.
