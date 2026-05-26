# linear connector (`linear://`)

Linear issues as `/teams/<team-key>/issues.jsonl` + `/users.jsonl`. Backed by the
Linear GraphQL API (cursor-paginated). `issues.jsonl` is lazy.

object_kind = `record_collection`. Each issue is flattened to
`{identifier, title, description, priority, state, assignee, labels, createdAt,
updatedAt}`. **search** over `record_aggregate` chunks from `text_fields`
(typically `title`, `description`); `locator` = `{identifier: "ENG-42"}`, `lines`
null → `mfs cat <source> --locator '{"identifier":"ENG-42"}'`.

Config: `text_fields` `["title","description"]`, `locator_fields` `["identifier"]`,
`metadata_fields` (e.g. `state`). Auth: personal API key (raw `Authorization`
header, not Bearer). Restrict with `teams`.
