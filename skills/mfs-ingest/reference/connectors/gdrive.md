# gdrive connector — ingest

URI: `gdrive://<alias>`.

## How to obtain credentials

Google Drive uses **OAuth credentials**.

Two flows:

### Service account (recommended for shared access)

1. GCP Console → **APIs & Services → Library** → enable **Google Drive
   API**.
2. **Credentials → Create Credentials → Service account** → name +
   role (`Viewer` is enough for drive read).
3. Service account → **Keys** → Add key → JSON. Download.
4. **Share** the Drive folder(s) you want indexed with the service
   account's email (`...@<project>.iam.gserviceaccount.com`).

### User OAuth (one user's view)

1. Same console, OAuth client ID (Desktop app).
2. First-run browser flow on a machine with a display.
3. `token.json` cached next to credentials.

## Required toml fields

| key | what |
|---|---|
| `token` | path to credentials JSON (use `file:/abs/path.json` to be explicit) |

## URI tree

```
gdrive://<alias>/
└── (files and folders the credential can see)
```

Shape depends on what's shared with the service account / OAuth user.

## env: example

```toml
token = "file:/home/zhangchen/.mfs/gdrive-sa.json"
```

```bash
mfs add gdrive://acme-engineering --config /tmp/mfs-gdrive.toml
```

## Pitfalls

- **Nothing shared → empty tree**: the service account can't see
  anything not explicitly shared with it. ASK the user to confirm
  sharing was done.
- **Google Docs as native format**: Google Doc / Sheet / Slide files
  appear in Drive but their content needs the Workspace APIs to
  export. The connector exports them as plain text on the fly.
- **Shortcut files vs real files**: a "shortcut" in Drive is a
  reference, not the content. The connector follows shortcuts but
  permissions still apply to the target.
- **Drive quotas**: Workspace Drive has user-storage quotas;
  enumerating doesn't count but downloading does.
