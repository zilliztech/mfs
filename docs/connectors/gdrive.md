# Google Drive (`gdrive`)

The `gdrive` connector indexes files in a Google Drive. Google-native Docs,
Sheets, and Slides are exported to text or CSV-like content; regular files are
converted like any document tree.

## How MFS sees it

The tree mirrors the Drive folder structure the credential can see:

```text
gdrive://engineering/
└── Product/
    ├── Roadmap.txt        document  (exported from a Google Doc)
    └── Design.pdf         document
```

## Credentials

Google Drive uses a **user OAuth token JSON** — the `token.json` from Google's
OAuth flow, containing `refresh_token` / `client_id` / `client_secret`.
Service-account keys are **not** supported.

1. <https://console.cloud.google.com> → pick or create a project. If this is
   the first OAuth client in the project, finish the OAuth consent screen first
   and add the authorizing Google account as a test user when the app is in
   testing.
2. *APIs & Services → Library → Google Drive API* → **Enable**.
3. *APIs & Services → Credentials → Create Credentials → OAuth client ID* →
   choose **Desktop app** → **Create** → **Download JSON**. Save it locally as
   `client_secret.json`.
4. On a workstation with a browser, run the consent flow once:

    ```bash
    uv run --with google-auth-oauthlib python - <<'PY'
    from pathlib import Path
    from google_auth_oauthlib.flow import InstalledAppFlow

    scopes = ["https://www.googleapis.com/auth/drive.readonly"]
    flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", scopes)
    creds = flow.run_local_server(port=0)
    Path("token.json").write_text(creds.to_json())
    PY
    ```

5. Copy `token.json` to a path the **server process** can read, for example
   `/home/x/.mfs/gdrive-token.json`, and reference that absolute path from the
   TOML.

![Google Cloud Drive API page](https://github.com/user-attachments/assets/523b9021-f809-4b1f-a1ad-16703739c409)

![Google Cloud Create credentials menu](https://github.com/user-attachments/assets/fc27a2aa-8eaf-4338-ada4-067be970857d)

![Google Cloud OAuth desktop client form](https://github.com/user-attachments/assets/2872163a-18d2-4592-ac43-b7ffc49693ed)

The authorized user must already be able to see the files — their own files plus
anything explicitly shared with them. If you also use [`gmail`](gmail.md), request
`gmail.readonly` in the same consent step and one `token.json` drives both.

## Configuration

```toml
token = "file:/home/x/.mfs/gdrive-token.json"
```

Save the file as `gdrive.toml`, then probe from the same environment that can
reach the MFS server:

```bash
mfs connector probe gdrive://engineering --config ./gdrive.toml
mfs add gdrive://engineering --config ./gdrive.toml
```

## Sync and freshness

The connector uses each file's `modifiedTime` as its cursor; deletions are caught
by `full_scan`.

This is one of two connectors (with [`feishu`](feishu.md)) that honors
`--since`. The connector enumerates the whole Drive the credential can see, which
can be large, so for a big account index recent files first: estimate the size
(optionally with a `since` date), then `mfs add gdrive://<alias> --since <date>`
indexes only files modified on or after that date. Older files are left untouched
(never deleted) and can be added later by lowering `--since`.

## Search and browse

```bash
mfs search "roadmap" gdrive://engineering/Product/
mfs cat gdrive://engineering/Product/Roadmap.txt --range 1:80
mfs export gdrive://engineering/Product/Design.pdf /tmp/design.pdf
```

## Pitfalls

- The credential only sees files the user owns or that are shared with them.
- Headless server: the OAuth flow needs a browser the first time — run it on a
  workstation, then copy `token.json` over.
- 401/403 usually means a revoked token or a consent that missed
  `drive.readonly`; re-run the OAuth flow.
- Google-native files are exported; their comments are not indexed.
