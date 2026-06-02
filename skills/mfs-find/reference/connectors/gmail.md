# gmail connector — search & browse

## URI tree

```
gmail://<alias>/
└── labels/
    ├── INBOX/threads.jsonl
    ├── Work/threads.jsonl
    └── ...
```

One `threads.jsonl` per Gmail label, containing one record per thread
(NOT per message — Gmail groups by conversation natively).

## Record shape

Each record is one thread, with all messages flattened:

```json
{"id": "<thread-id>",
 "threadId": "<thread-id>",
 "subject": "...",
 "from": "alice@acme.com",
 "to": "bob@acme.com",
 "date": "2026-06-01T...",
 "labelIds": ["INBOX", "Work"],
 "snippet": "...",
 "body": "<full thread body, messages concatenated>"}
```

## Chunk kind

`thread_aggregate` — one chunk per thread by default. Long threads may
get sub-chunked.

## Locator

```bash
mfs cat gmail://<alias>/labels/INBOX/threads.jsonl \
  --locator '{"threadId": "<id>", "id": "<message-id>"}'
```

Both `threadId` and `id` participate in the locator (handles cases
where you want the thread vs a specific message).

## Search strategy

| Intent | Use |
|---|---|
| "Email about X" | `mfs search "X" gmail://<alias>` |
| Scoped by label | `mfs search "X" gmail://<alias>/labels/Work/threads.jsonl` |
| Sender filter | semantic search picks up email addresses fine; or use metadata filter on `from` |
| Date filter | `metadata.date` is preserved; client-side filter |

## Pitfalls

- **Labels are case-sensitive**: `Work` ≠ `work`. Custom labels in the
  toml must match exactly.
- **Big inboxes**: 100k+ threads can take hours to first-sync.
  `max_read_rows` cap recommended for initial test.
- **`body` may be HTML**: Gmail messages often have both
  `text/plain` and `text/html` parts. The connector extracts the
  plain-text alternative when present; otherwise strips HTML tags.
  Embedded images/attachments are NOT indexed.
- **Attachments invisible**: PDF / image attachments are NOT extracted
  in v1. The thread body alone is searchable.
