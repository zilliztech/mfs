# feishu connector (`feishu://` — Feishu / Lark)

Feishu group chats as `/chats/<name>__<chat-id>/messages.jsonl` — one
**message_stream** per chat, lazy (paged from the lark-oapi `im.v1.message.list`).

**search** runs over `thread_aggregate` chunks (messages grouped per thread/root via
`thread_id`/`root_id`, else per message). Each message is flattened to
`{message_id, msg_type, create_time, sender, thread_id, text}` (text extracted from
the `text`/`post` message bodies). `locator` = `{thread: "<id>"}`, `lines` null →
`mfs cat <source> --locator '{"thread":"<id>"}'`.

Config: `text_fields` (e.g. `["sender","text"]`), `chunk_strategy="per_group"`.
Auth: app `app_id` + `app_secret` (the SDK manages tenant tokens).
