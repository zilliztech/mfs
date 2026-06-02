# zendesk connector — search & browse

## URI tree

```
zendesk://<alias>/
├── tickets.jsonl                ← tickets
├── tickets_comments.jsonl       ← all ticket comments (joined with ticket_id)
├── users.jsonl                  ← agents + end-users
└── organizations.jsonl
```

`tickets_comments.jsonl` is a virtual join — comments tagged with
their parent `ticket_id` so a search like "what did support say about
billing" finds the conversation, not just the ticket subject.

## Record shapes

**Ticket**:
```json
{"id": 12345, "subject": "...", "description": "...", "status": "open",
 "priority": "normal", "tags": ["billing", "v2"], "requester_id": 678,
 "assignee_id": 901, "updated_at": "2026-06-01T..."}
```

**Ticket comment**:
```json
{"id": 9876, "ticket_id": 12345, "author_id": 901,
 "body": "Hi, we've fixed the billing issue...", "public": true,
 "created_at": "..."}
```

**User**:
```json
{"id": 678, "name": "Alice Wang", "email": "alice@acme.com",
 "role": "end-user", "organization_id": 42}
```

## Chunk kinds

`row_text` for everything. Content shape depends on configured
`text_fields`:
- tickets: `subject + description`
- comments: `body`
- users: `name + email`

## Locator

```bash
mfs cat zendesk://<alias>/tickets.jsonl --locator '{"id": 12345}'
mfs cat zendesk://<alias>/tickets_comments.jsonl --locator '{"id": 9876}'
```

## Search strategy

| Intent | Use |
|---|---|
| Find tickets about X | `mfs search "X" zendesk://<alias>/tickets.jsonl` |
| What did support say | `mfs search "X" zendesk://<alias>/tickets_comments.jsonl` (richer recall) |
| Find a user | `mfs search "X" zendesk://<alias>/users.jsonl` |

## Pitfalls

- **`tickets_comments` is a join, not paginated raw**: ingest fetches
  comments per ticket → expensive on big tenants. Default
  `max_read_rows` caps total comments.
- **Suspended tickets**: filtered out at the source by Zendesk.
- **Public vs internal comments**: `public: false` comments include
  agent internal notes. They're indexed — be aware searching might
  return internal comms.
- **Side-loaded relations**: org of a user, brand of a ticket — only
  the IDs are in the record. Names require cross-lookup against
  `organizations.jsonl` / `users.jsonl`.
