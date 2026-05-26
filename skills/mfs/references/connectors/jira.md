# jira connector (`jira://`)

Jira issues as `/projects/<proj>/issues.jsonl` + `/users.jsonl`. `issues.jsonl`
is **lazy** (paged from the JQL API); single issues aren't separate paths.

object_kind = `record_collection`. Each issue is flattened to
`{key, summary, description, status, priority, assignee, reporter, labels,
created, updated}`. **search** runs over `record_aggregate` chunks built from
configured `text_fields` (typically `summary`, `description`); each hit's
`locator` = `{key: "ENG-123"}`, `lines` null → reopen with
`mfs cat <source> --locator '{"key":"ENG-123"}'`.

Config: pick `text_fields` (e.g. `["summary","description"]`), `locator_fields`
`["key"]`, `metadata_fields` (e.g. `status`,`priority`) for filtering. Auth: cloud
uses email + API token; server uses a PAT (`token`). Restrict with `projects`.
