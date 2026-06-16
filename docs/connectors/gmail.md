# Gmail (`gmail`)

The `gmail` connector indexes mail under chosen labels, grouped into threads. Use
it to search a mailbox by meaning — "the contract renewal thread" — rather than by
remembering sender and date.

## How MFS sees it

Each label exposes a message stream; messages are grouped by Gmail `threadId`:

```text
gmail://work/
└── labels/
    ├── INBOX__INBOX/messages.jsonl
    └── INBOX__CATEGORY_PERSONAL/messages.jsonl
```

The `gmail.messages` preset embeds subject, from/to, body, and snippet per
thread, so no `[[objects]]` config is needed.

## Credentials

Gmail uses a **user OAuth token JSON** — the `token.json` from Google's OAuth
flow, containing `refresh_token` / `client_id` / `client_secret`. Service-account
keys are **not** supported.

1. <https://console.cloud.google.com> → pick or create a project.
2. *APIs & Services → Library → Gmail API* → **Enable**.
3. *Credentials → Create Credentials → OAuth client ID* → *Desktop app* →
   **Download JSON** (the client credentials, not the token yet).
4. Run Google's OAuth flow once on a machine with a browser (e.g.
   `InstalledAppFlow.run_local_server`) requesting scope
   `https://www.googleapis.com/auth/gmail.readonly`. This writes `token.json`.
5. Copy `token.json` to the server and reference it from the TOML.

The connector only calls `messages.list` + `messages.get` — it never sends or
modifies mail. If you also use [`gdrive`](gdrive.md), request `drive.readonly` in
the same consent step and one `token.json` drives both.

## Configuration

```toml
token = "file:/home/x/.mfs/gmail-token.json"
labels = ["INBOX"]            # empty = all labels
max_read_rows = 5000
```

`token` is usually a `file:` reference to `token.json`. The plugin also accepts a
bare access-token string or inline token JSON, but the file reference is the
common form.

## Sync and freshness

The connector uses Gmail's `historyId` as its cursor for incremental re-sync.
Deletion detection is `never` — removed mail isn't retroactively pruned.

## Search and browse

```bash
mfs add gmail://work --config ./gmail.toml

mfs search "contract renewal" gmail://work/labels/INBOX__CATEGORY_PERSONAL/messages.jsonl
mfs cat gmail://work/labels/INBOX__CATEGORY_PERSONAL/messages.jsonl --locator '{"threadId":"THREAD_ID","id":"MESSAGE_ID"}'
```

## Pitfalls

- Label matching uses Gmail label names or the IDs the API returns.
- Attachments are not indexed.
- Large labels can hit `max_read_rows` and produce partial recall.
