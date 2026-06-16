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

1. GCP Console → *APIs & Services → Library* → enable **Google Drive API**.
2. *Credentials → Create Credentials → OAuth client ID* → *Desktop app* →
   **Download JSON** (the client credentials).
3. Run Google's OAuth flow once on a machine with a browser (e.g.
   `InstalledAppFlow.run_local_server`) requesting scope
   `https://www.googleapis.com/auth/drive.readonly`. This writes `token.json`.
4. Copy `token.json` to the server and reference it from the TOML.

The authorized user must already be able to see the files — their own files plus
anything explicitly shared with them. If you also use [`gmail`](gmail.md), request
`gmail.readonly` in the same consent step and one `token.json` drives both.

## Configuration

```toml
token = "file:/home/x/.mfs/gdrive-token.json"
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
mfs add gdrive://engineering --config ./gdrive.toml

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
