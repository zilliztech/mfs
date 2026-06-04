# slack connector — search & browse

## URI tree

```
slack://<alias>/
├── channels/
│   ├── general__C012345/messages.jsonl     ← per-channel message stream
│   ├── eng-backend__C067890/messages.jsonl
│   └── ...
└── users.jsonl                              ← workspace member directory
```

Channel directory names are `<name>__<id>` so the channel id is always
recoverable even after a rename.

## Message record shape

One JSON per message in `messages.jsonl`:

```json
{"ts": "1717123456.001200",         ← unique key within the channel
 "user": "U012345",                  ← member id (resolve via /users.jsonl)
 "text": "部署炸了, 503 spike",
 "thread_ts": "1717123456.001200",   ← parent message id; for top-level msgs, equals own ts
 "channel": "C012345",
 "reactions": [...], "blocks": [...]}
```

The connector populates `thread_ts` for every message (top-level msgs
get `thread_ts = ts`), so thread aggregation is uniform.

## User record shape

One JSON per member in `/users.jsonl`:

```json
{"id": "U012345", "name": "alice", "real_name": "Alice Wang",
 "profile": {"display_name": "Alice (eng)", "email": "alice@acme.com",
             "title": "Senior Engineer"},
 "is_admin": false, "deleted": false}
```

## Chunk kinds

- **`thread_aggregate`** (per Slack thread, in messages.jsonl): all
  messages with the same `thread_ts` are joined in order into one
  chunk. Long threads split into sub-chunks at message boundaries
  with 2-message overlap. The chunk content is rendered as
  `<user>: <text>\n\n<user>: <text>...`
- **`row_text`** (per user, in users.jsonl): one chunk per workspace
  member. Content includes name + real_name + display_name + title +
  email.

## Locator

| Chunk | Locator shape |
|---|---|
| Thread (short) | `{"thread_ts": "1717123456.001200"}` |
| Thread (long, sub-chunk) | `{"thread_ts": "...", "chunk_index": 1, "msg_range": [40, 78]}` |
| User | `{"id": "U012345"}` |

```bash
mfs cat slack://acme/channels/eng-backend__C067890/messages.jsonl \
  --locator '{"thread_ts": "1717123456.001200"}'
```

## Search strategy

| Intent | Use |
|---|---|
| "What did we say about X" | `mfs search "X" slack://<alias>` — returns whole threads (not single messages) |
| Scoped to one channel | `mfs search "X" slack://<alias>/channels/<name>__<id>/messages.jsonl` |
| Who is X person | `mfs search "Y" slack://<alias>/users.jsonl` — works because users are individually indexed |
| Find user by email/name | same as above, semantic + keyword both work |
| List channels | `mfs ls slack://<alias>/channels` |

## Field semantics

- `user` (in a message) is a member ID like `U012345`. To resolve to a
  human name, cross-reference `/users.jsonl` — or just `mfs cat
  --locator '{"id": "U012345"}'` against users.jsonl.
- `text` may contain Slack-formatted mentions (`<@U012345>`), links
  (`<https://...|label>`), and channel refs (`<#C067890|name>`). The
  connector doesn't unwrap these — they appear verbatim in the chunk.

## Pitfalls

- **Thread aggregation makes hits longer than expected**: search a
  short phrase, get back a thread of 20 messages. Expected — the
  chunk IS the thread.
- **Bot's own messages**: bot-posted messages are indexed too (e.g.
  GitHub notifications). For some workspaces this is noise; filter at
  search time on `metadata.user` or set up an `[[objects]]` filter.
- **`/users.jsonl` may be `available` but `mfs search` returns no user
  hits**: connector might be on an older sync that pre-dates the
  users-indexing feature. Run `mfs add slack://<alias>` to pick it up;
  use `--full` only when cached chunks must be rebuilt.
