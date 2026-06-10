# gdrive connector — ingest

URI: `gdrive://<alias>`.

## How to obtain credentials

Google Drive uses a **user OAuth token JSON** (the `token.json` produced
by Google's OAuth flow — contains `refresh_token`, `client_id`,
`client_secret`). Service-account keys are not supported by the current
plugin.

1. GCP Console → **APIs & Services → Library** → enable **Google Drive
   API**.
2. **Credentials → Create Credentials → OAuth client ID** → Application
   type: **Desktop app** → download the client JSON.
3. Run Google's OAuth flow once on a machine with a browser (e.g. the
   `google-auth-oauthlib` `InstalledAppFlow.run_local_server` snippet)
   requesting scope
   `https://www.googleapis.com/auth/drive.readonly`. The flow writes a
   `token.json` next to the client JSON.
4. Copy `token.json` to the server and point `token` at it.

If you also want gmail, add `https://www.googleapis.com/auth/gmail.readonly`
to the same consent step — the resulting `token.json` then works for
both connectors. See [[gmail]].

## Required toml fields

| key | what |
|---|---|
| `token` | path to the user OAuth token JSON (use `file:/abs/path/token.json` to be explicit) |

## URI tree

```
gdrive://<alias>/
└── (files and folders the credential can see)
```

Shape depends on what the authorized user can see in Drive (own files +
files explicitly shared with them).

## env: example

```toml
token = "file:/home/zhangchen/.mfs/gdrive-token.json"
```

```bash
mfs add gdrive://acme-engineering --config /tmp/mfs-gdrive.toml
```

## Limiting scope (large Drives)

The connector enumerates the **whole** Drive the credential can see. For a big
account, index recent files first instead of everything:

- Estimate the size first (optionally with a `since` date via
  `/v1/connectors/estimate`) so the user sees the count before any work.
- Add with a start date: `mfs add gdrive://<alias> --config ... --since <date>`.
  Only files modified on/after `<date>` are indexed; older files are left alone
  (never deleted) and can be pulled in later by lowering `--since`.

## Pitfalls

- **Headless server**: the OAuth flow needs a browser the first time.
  Run it on a workstation, then copy `token.json` to the server.
- **Token revoked / scope mismatch**: 401/403s usually mean the token
  was revoked or the consent didn't include `drive.readonly`. Re-run
  the OAuth flow.
- **Google Docs as native format**: Google Doc / Sheet / Slide files
  appear in Drive but their content needs the Workspace APIs to
  export. The connector exports them as plain text on the fly.
- **Shortcut files vs real files**: a "shortcut" in Drive is a
  reference, not the content. The connector follows shortcuts but
  permissions still apply to the target.
- **Drive quotas**: Workspace Drive has user-storage quotas;
  enumerating doesn't count but downloading does.
