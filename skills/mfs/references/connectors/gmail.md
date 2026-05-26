# gmail connector (`gmail://`)

Gmail as `/labels/<label>__<label-id>/messages.jsonl` — one **message_stream** per
label, lazy (paged from the Gmail API; each message fetched `format=full`).

**search** runs over `thread_aggregate` chunks: messages are grouped by `threadId`,
so a conversation becomes one chunk. Each message is flattened to
`{id, threadId, subject, from, to, date, snippet, body}`. `locator` =
`{threadId: "..."}`, `lines` null → reopen the thread with
`mfs cat <source> --locator '{"threadId":"..."}'`.

Config: `text_fields` (e.g. `["subject","from","body"]`), `chunk_strategy="per_group"`
(default thread-key fallback recognizes `threadId`). Auth: OAuth user credentials
(`token`, authorized-user JSON). Restrict with `labels`.
