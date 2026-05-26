# slack connector (`slack://`)

Slack channels as `/channels/<name>__<channel-id>/messages.jsonl` + `/users.jsonl`.
The dir name always carries the channel id (names repeat). `messages.jsonl` is a
**message_stream** and is lazy (paged from `conversations.history`).

**search** runs over `thread_aggregate` chunks: messages are grouped by `thread_ts`
(a root message + its replies become ONE chunk), so a hit returns the whole thread,
not a single line. `locator` = `{thread_ts: "1716..."}`, `lines` null → reopen the
thread with `mfs cat <source> --locator '{"thread_ts":"1716..."}'`.

Config in `[[objects]]`: `text_fields` (e.g. `["user","text"]`),
`chunk_strategy="per_group"`, `group_by="thread_ts"` (the default thread-key
fallback also recognizes this). Auth: bot `token` (xoxb-...). Restrict with
`channel_types` / `oldest`.
