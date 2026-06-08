# gmail connector — ingest

URI: `gmail://<alias>` (alias is your Gmail account nickname; common:
`inbox`, `work`, `personal`).

## How to obtain credentials

Gmail uses a **user OAuth token JSON** (the `token.json` produced by
Google's OAuth flow — contains `refresh_token`, `client_id`,
`client_secret`). The plugin reads only this token; it doesn't run an
OAuth dance for you.

1. Go to <https://console.cloud.google.com> → create or pick a project.
2. **APIs & Services → Library → Gmail API** → Enable.
3. **APIs & Services → Credentials → Create Credentials → OAuth client
   ID** → Application type: **Desktop app** → download the client JSON.
4. Run Google's OAuth flow once on a machine with a browser (e.g. the
   `google-auth-oauthlib` `InstalledAppFlow.run_local_server` snippet),
   requesting scope
   `https://www.googleapis.com/auth/gmail.readonly`. The flow writes a
   `token.json` next to the client JSON.
5. Copy `token.json` to the server and point `token` at it.

The connector calls `messages.list` + `messages.get` only; it does not
send or modify mail.

If you also want gdrive, add `https://www.googleapis.com/auth/drive.readonly`
to the same consent step — the resulting `token.json` then works for
both connectors. See [[gdrive]].

## Required toml fields

| key | what |
|---|---|
| `token` | path to the user OAuth token JSON (use `file:/abs/path/token.json` to be explicit) |

## Optional

| key | default | meaning |
|---|---|---|
| `labels` | _all_ | label list to index (e.g. `["INBOX", "Work"]`) |
| `max_read_rows` | 20000 | per-label thread cap |

## env: example

```toml
token = "file:/home/zhangchen/.mfs/gmail-token.json"
labels = ["INBOX", "Engineering"]
max_read_rows = 5000
```

```bash
mfs add gmail://work --config /tmp/mfs-gmail.toml
```

## Pitfalls

- **Headless server**: the OAuth flow needs a browser the first time.
  Run it on a workstation, then copy `token.json` to the server.
- **Token revoked / scope mismatch**: 401/403s usually mean the token
  was revoked or the consent didn't include `gmail.readonly`. Re-run
  the OAuth flow.
- **Label names**: Gmail's "system" labels are `INBOX`, `SENT`,
  `DRAFT`, `TRASH`, `SPAM`, `IMPORTANT`. User labels are case-sensitive
  and exact-match.
- **One thread = one record**: the Gmail connector groups by Gmail's
  threadId, which is already conversation-level. No `group_by`
  re-aggregation needed.
- **Big inboxes**: 100k+ threads = significant first-sync time;
  `max_read_rows` cap helps if you only need recent.
