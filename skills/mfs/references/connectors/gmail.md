# gmail connector (`gmail://`)

## What this is

Gmail for a specific user account. Uses `google-api-python-client` (sync,
wrapped in `asyncio.to_thread`). Each label is exposed as a message stream;
the engine groups by `threadId` and emits thread-aggregate chunks (full
conversations).

**When MFS helps**: a shared support inbox, sales inbox, or your own
archive — you want to find "the thread where customer X discussed renewal
terms in Feb" without scrolling.

## URI shape

```
gmail://<alias>/                                            connector root
gmail://<alias>/labels/                                     Gmail labels (incl. system + user)
gmail://<alias>/labels/INBOX__INBOX/messages.jsonl          one label's stream
gmail://<alias>/labels/Sales__Label_12/messages.jsonl       user label (suffix = label-id)
```

Each message in the stream has `{id, threadId, subject, from, to, date,
snippet, body}` after flattening.

## Auth — OAuth user credentials

Same model as **gdrive**: authenticate ONCE as the user (Gmail account
owner) via OAuth, save the resulting authorized-user JSON to a file on
the MFS server.

```toml
credential_ref = "file:/var/run/secrets/gmail/token.json"
```

### One-time setup

1. GCP Console → enable **Gmail API**.
2. OAuth client (Desktop) — download client_secrets.json.
3. Run a one-shot OAuth flow with scope
   `https://www.googleapis.com/auth/gmail.readonly`. Save the resulting
   authorized-user JSON (`{type: "authorized_user", refresh_token, …}`) to
   the MFS server path referenced above.

For Workspace accounts you can use **domain-wide delegation** with a
service account — but that requires admin setup and isn't currently
implemented in the connector. OAuth user-account flow only today.

## Connector config TOML

```toml
# ─── auth (required) ───
credential_ref = "file:/var/run/secrets/gmail/token.json"

# ─── scope ───
# labels = ["INBOX", "Sales", "Support"]   # restrict to these labels; default = all visible
# max_read_rows = 50000                     # cap per label

# PRESET 'gmail.messages' applied automatically:
#   group_by = "threadId"
#   text_fields = ["subject", "from", "to", "body", "snippet"]
#   metadata_fields = ["from", "to", "date", "labelIds[*]"]
#   locator_fields = ["threadId", "id"]
```

## What each command does

| Command | Behaviour |
|---|---|
| `mfs ls /labels/` | `users.labels.list(userId="me")`. |
| `mfs ls /labels/<name>__<id>/` | `["messages.jsonl"]`. |
| `mfs cat .../messages.jsonl --range A:B` | `messages.list(labelIds=[...], pageToken=...)` paginated, then `messages.get(format=full)` per message. |
| `mfs cat .../messages.jsonl --locator '{"threadId":"..."}'` | `threads.get(id=...)` — full thread. |
| `mfs search "QUERY"` | Milvus only. Hits at thread granularity (one chunk per thread, sub-chunked when long). |

## Typical workflow

```bash
# 1. OAuth dance → save token.json to /var/run/secrets/gmail/

# 2. Register.
cat > gmail-support.toml <<'EOF'
credential_ref = "file:/var/run/secrets/gmail/token.json"
labels = ["INBOX", "Support"]
EOF
mfs add gmail://support --config gmail-support.toml

# 3. Search.
mfs search "Q4 renewal pricing tier discussion" --connector-uri gmail://support
mfs cat gmail://support/labels/Support__Label_12/messages.jsonl --locator '{"threadId":"18d..."}'

# 4. Refresh.
mfs add gmail://support --no-full
```

## Incremental sync

Per-label fingerprint = `historyId` from the Gmail user profile. Refresh
uses `users.history.list(startHistoryId=...)` to fetch only changes since
last sync — the cheapest possible delta scan Google provides.

## Gotchas

1. **OAuth user account only** — no service account / domain-wide
   delegation in this version. For a Workspace deployment that wants
   to index a shared "support@" inbox, OAuth as that mailbox's owner.
2. **`gmail.readonly` scope** is enough; do NOT grant `gmail.modify`
   unless you need it. Principle of least privilege.
3. **Each `messages.get(format=full)` is its own API call** — the initial
   sync is slow for inboxes with 10k+ messages. Gmail's quota is ~250
   units/user/second; the connector throttles naturally but plan for
   minutes-to-hours on first sync.
4. **Body parsing**: HTML mails are decoded to plain text (best-effort).
   The `body` field is the plain-text part if present, else converted
   from the HTML part. Attachments are NOT fetched today.
5. **Threads vs messages**: a thread is one chunk regardless of how many
   messages it contains, until total content exceeds 1500 chars, at
   which point the long-thread sub-chunking kicks in (chunks share
   `threadId` but have distinct `chunk_index`).
6. **No webhook live mode** — refresh via `--no-full`. Gmail does have
   push notifications via Pub/Sub but that's a future integration.
