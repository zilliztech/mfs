# discord connector — search & browse

## URI tree

```
discord://<alias>/
└── channels/
    ├── general__987654321/
    │   ├── messages.jsonl                            ← main channel stream
    │   └── threads/
    │       ├── deploy-outage__111/messages.jsonl     ← thread channels
    │       └── feature-rfc__222/messages.jsonl
    ├── announcements__123456789/messages.jsonl
    └── eng-team__555666777/messages.jsonl
```

Discord threads are independent child channels with their own IDs
(type 11/10/12), nested under their parent under `/threads/`. Only
**active** threads are enumerated; archived threads aren't included
in v1.

## Message record shape

```json
{"id": "1234567890123456789",        ← snowflake; globally unique
 "channel_id": "987654321",
 "author": {"id": "111", "username": "alice"},
 "content": "Anyone seen the deploy fail?",
 "timestamp": "2026-06-02T12:34:56.789Z",
 "type": 0,
 "referenced_message": {...}          ← if this is a reply
 // NOTE: no `thread_id` field on a normal message; thread membership
 // is implicit in the channel_id.
}
```

## Chunk kind

`row_text` — Discord messages are NOT thread-aggregated (no per-message
`thread_id` field to group on). Each message is its own chunk. Content
is rendered as `<author.username>: <content>`.

## Locator

```bash
mfs cat discord://<alias>/channels/<parent>__<id>/messages.jsonl \
  --locator '{"id": "1234567890123456789"}'
```

## Search strategy

| Intent | Use |
|---|---|
| Find conversation about X | `mfs search "X" discord://<alias>` — top hits are individual messages |
| Thread content | thread channels appear as their own `messages.jsonl`, search returns those messages directly |
| Replies / context | each hit is one message; use `mfs cat --range` on the parent channel to see surrounding messages |
| User mentions | `mfs grep "@username" <path>` (note: usernames may appear as `<@111>` ID refs in `content`) |

## Field semantics

- `id` is a Discord snowflake (encodes timestamp + worker + sequence).
- `author.username` is plain text (no resolution needed).
- Messages that reference another message carry `referenced_message`
  (the full object of what was replied to). Useful context for the
  embedding.

## Pitfalls

- **Archived threads invisible**: only `GET /guilds/{id}/threads/active`
  is consulted. Long-archived threads aren't reachable via this
  connector in v1. Search will simply not find them.
- **Voice / stage / forum channels skipped**: only `GUILD_TEXT` (type
  0) and `GUILD_ANNOUNCEMENT` (type 5) are enumerated.
- **No automatic thread-aggregation**: unlike Slack/Feishu, a Discord
  "conversation" doesn't come back as one chunk. Multiple messages
  about the same topic come back as separate hits.
- **`content` may be empty** if the Message Content Intent isn't
  granted on the bot. Symptom: every search misses; `mfs head` shows
  empty `content` fields.
