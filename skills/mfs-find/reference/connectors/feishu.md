# feishu (lark) connector — search & browse

## URI tree

```
feishu://<alias>/
├── chats/
│   ├── <chat-name>__<chat-id>/messages.jsonl     ← group / direct chats
│   └── ...
└── docs/
    ├── <title>__<doc-token>.md                   ← docx documents
    └── ...
```

Two subtrees in one connector: live group chats AND docx documents.
Both subtrees scope per `auth` mode and tenant config.

## Message record shape

```json
{"message_id": "om_abc123",
 "msg_type": "text",
 "create_time": "1717123456000",
 "sender": "ou_xyz789",                    ← user open_id
 "thread_id": "om_abc123",                 ← parent message id; populated by plugin
 "text": "部署炸了"}
```

The plugin populates `thread_id` even for top-level messages (falls
back to own `message_id`), so thread aggregation is uniform.

## Doc record shape

`docs/*.md` are plain markdown converted from Feishu docx, served as
file-like objects (not record collections). Each doc is one searchable
object; the content is the rendered markdown body.

## Chunk kinds

- **`thread_aggregate`** in `messages.jsonl` (thread-aggregated, same
  pattern as Slack — by `thread_id`)
- **`chunk_body`** in `*.md` (recursive chunker splits long docx into
  size-bounded chunks at heading boundaries)

## Locator

| Chunk | Locator |
|---|---|
| Thread (short) | `{"message_id": "om_abc123"}` (note: locator uses message_id, not thread_id) |
| Thread (sub-chunk) | adds `chunk_index` + `msg_range` |
| Docx chunk | `{"lines": [start, end]}` |

## Search strategy

| Intent | Use |
|---|---|
| "What did we discuss about X in the chat" | `mfs search "X" feishu://<alias>/chats/` |
| "Find the design doc about Y" | `mfs search "Y" feishu://<alias>/docs/` |
| Workspace-wide | `mfs search "Z" feishu://<alias>` |

## Field semantics

- `sender` is an `ou_*` user open_id, not a readable name. To resolve
  open_id → name, you currently have to call the Feishu API outside
  MFS — there's no `users.jsonl` like Slack has.
- `msg_type` values: `text`, `post`, `image`, `file`, … only `text`
  and `post` produce searchable content (text extraction).
- `docs/` subtree only appears in OAuth mode (user identity) OR when
  the bot has docx scope + has been shared with documents.

## Pitfalls

- **OAuth vs tenant scope**: tenant-auth misses chats the bot isn't
  added to, and most p2p chats are unreachable in tenant mode (Feishu
  API limit). If "I know the chat exists but search returns nothing",
  switch to OAuth mode at ingest time.
- **Region split**: `feishu` (open.feishu.cn) and `lark` (open.larksuite.com)
  are separate; one connector covers one region.
- **Doc rendering loses formatting**: tables, mentions, and inline
  links may flatten in the markdown export. Native docx features like
  comments are NOT in the indexed content.
