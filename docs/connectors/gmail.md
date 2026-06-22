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

1. <https://console.cloud.google.com> → pick or create a project. If this is
   the first OAuth client in the project, finish the OAuth consent screen first
   and add the authorizing Google account as a test user when the app is in
   testing.
2. *APIs & Services → Library → Gmail API* → **Enable**.
3. *APIs & Services → Credentials → Create Credentials → OAuth client ID* →
   choose **Desktop app** → **Create** → **Download JSON**. Save it locally as
   `client_secret.json`.
4. On a workstation with a browser, run the consent flow once:

    ```bash
    uv run --with google-auth-oauthlib python - <<'PY'
    from pathlib import Path
    from google_auth_oauthlib.flow import InstalledAppFlow

    scopes = ["https://www.googleapis.com/auth/gmail.readonly"]
    flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", scopes)
    creds = flow.run_local_server(port=0)
    Path("token.json").write_text(creds.to_json())
    PY
    ```

5. Copy `token.json` to a path the **server process** can read, for example
   `/home/x/.mfs/gmail-token.json`, and reference that absolute path from the
   TOML.

![Google Cloud Gmail API page](https://github.com/user-attachments/assets/8a2b96ab-8ea6-4442-9e25-f186c879dd14)

![Google Cloud Create credentials menu](https://github.com/user-attachments/assets/fc27a2aa-8eaf-4338-ada4-067be970857d)

![Google Cloud OAuth desktop client form](https://github.com/user-attachments/assets/2872163a-18d2-4592-ac43-b7ffc49693ed)

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

Save the file as `gmail.toml`, then probe and index:

```bash
mfs connector probe gmail://work --config ./gmail.toml
mfs add gmail://work --config ./gmail.toml
```

## Sync and freshness

The connector uses Gmail's `historyId` as its cursor for incremental re-sync.
Deletion detection is `never` — removed mail isn't retroactively pruned.

## Search and browse

```bash
mfs search "contract renewal" gmail://work/labels/INBOX__CATEGORY_PERSONAL/messages.jsonl
mfs cat gmail://work/labels/INBOX__CATEGORY_PERSONAL/messages.jsonl --locator '{"threadId":"THREAD_ID","id":"MESSAGE_ID"}'
```

## Pitfalls

- Label matching uses Gmail label names or the IDs the API returns.
- Attachments are not indexed.
- Large labels can hit `max_read_rows` and produce partial recall.
