# discord connector (`discord://`)

Discord text channels as `/channels/<name>__<channel-id>/messages.jsonl`.
`messages.jsonl` is a **message_stream**, lazy (paged from the REST messages API,
newest-first with `before`).

**search** runs over `thread_aggregate` chunks (messages grouped per thread/root;
plain channel messages group by their own id). A hit returns the grouped block;
`locator` = `{thread: "<id>"}`, `lines` null → `mfs cat <source> --locator '{"thread":"<id>"}'`.

Config: `text_fields` (e.g. `["author","content"]` — flatten as needed),
`chunk_strategy="per_group"`. Auth: bot `token` (header `Authorization: Bot ...`),
`guild_id` selects the server. Only text channels (types 0/5) are exposed.
