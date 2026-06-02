# gmail connector — ingest

URI: `gmail://<alias>` (alias is your Gmail account nickname; common:
`inbox`, `work`, `personal`).

## How to obtain credentials

Gmail uses **Google OAuth 2.0** with a downloaded credentials JSON.

1. Go to <https://console.cloud.google.com> → create or pick a project.
2. **APIs & Services → Library → Gmail API** → Enable.
3. **APIs & Services → Credentials → Create Credentials → OAuth client
   ID** → Application type: Desktop app → name it → Download JSON.
4. Save the file (e.g. `~/.mfs/gmail-credentials.json`).
5. First-time `mfs add` will open a browser to authorize; the resulting
   `token.json` is cached next to credentials.

Required OAuth scopes:
- `https://www.googleapis.com/auth/gmail.readonly`

The connector reads `messages.list` + `messages.get`; doesn't send or
modify.

## Required toml fields

| key | what |
|---|---|
| `token` | path to the OAuth credentials file (e.g. `/home/x/.mfs/gmail-credentials.json`) — NOT the value, the file path. Use `file:/abs/path/to.json` to be explicit. |

## Optional

| key | default | meaning |
|---|---|---|
| `labels` | _all_ | label list to index (e.g. `["INBOX", "Work"]`) |
| `max_read_rows` | 20000 | per-label thread cap |

## env: example

```toml
token = "file:/home/zhangchen/.mfs/gmail-credentials.json"
labels = ["INBOX", "Engineering"]
max_read_rows = 5000
```

```bash
mfs add gmail://work --config /tmp/mfs-gmail.toml
# first run opens a browser for OAuth consent
# subsequent runs use the cached token.json
```

## Pitfalls

- **First-run browser opens** — on a headless server, can't do this.
  Run the OAuth dance on a workstation first, then copy
  `credentials.json` + `token.json` to the server.
- **Token expiry**: Google refresh tokens are long-lived but can be
  revoked. Re-run the OAuth dance if connector starts 401-ing.
- **Label names**: Gmail's "system" labels are `INBOX`, `SENT`,
  `DRAFT`, `TRASH`, `SPAM`, `IMPORTANT`. User labels are case-sensitive
  and exact-match.
- **One thread = one record**: the Gmail connector groups by Gmail's
  threadId, which is already conversation-level. No `group_by`
  re-aggregation needed.
- **Big inboxes**: 100k+ threads = significant first-sync time;
  `max_read_rows` cap helps if you only need recent.
