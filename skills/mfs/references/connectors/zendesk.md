# zendesk connector (`zendesk://`)

Support data as record collections:
- `/tickets/records.jsonl` — all tickets
- `/tickets/comments.jsonl` — all comments, each tagged with `ticket_id`
- `/users/records.jsonl`, `/organizations/records.jsonl`

All lazy (cursor-paginated REST). object_kind = `record_collection`. **search**
over `record_aggregate` chunks from configured `text_fields` (e.g. ticket
`subject`,`description`; comment `body`); `locator` = `{id: <n>}` (comments also
`{ticket_id}`), `lines` null → reopen with `mfs cat <source> --locator '{"id":123}'`.

Config: `text_fields`/`locator_fields`/`metadata_fields` per collection in
`[[objects]]`. Auth: `subdomain` + `email` + API `token` (basic auth `email/token`).
