# gdrive connector (`gdrive://` — Google Drive)

## What this is

Google Drive (personal or Workspace) mirrored as a filesystem tree. Native
Google formats (Docs / Sheets / Slides) are **exported** to text/CSV on read
so they're indexable; regular uploads (PDFs, images, code) keep their bytes
and route through the same per-extension pipeline as the **file** connector.

**When MFS helps**: a Drive folder with hundreds of docs, RFCs, design
specs, sheet-based registries — you want unified search alongside your
code/db data.

**Cost note**: Drive API is free per user-quota; the embedding cost is
borne by your configured embedding provider. Native-doc exports happen on
sync + on `cat` (cached in transformation cache after first export).

## URI shape

```
gdrive://my-drive/                              connector root (the auth'd user's Drive)
gdrive://my-drive/Engineering/RFCs/RFC-042.txt  exported Google Doc
gdrive://my-drive/Sales/Pipeline.csv            exported Google Sheet
gdrive://my-drive/Designs/v3-hero.png           binary upload (image → VLM)
gdrive://my-drive/Reports/Q1.pdf                binary upload (pdf → converted md)
```

Folder structure is reconstructed from each file's `parents` list (Drive's
flat-bag-of-files-with-parents model → MFS makes a tree). Files in multiple
folders appear in only the first parent (limitation of the materialised
tree).

## Auth — OAuth (user credentials)

This connector authenticates as a **specific Google user** via OAuth — there
is no service-account path. You need a desktop / installed-app OAuth client
in Google Cloud Console, then to walk the user through the consent flow
once and save the resulting credentials JSON.

### One-time setup

1. **GCP Console** → "APIs & Services" → "Library" → enable **Google Drive API**.
2. **APIs & Services** → "Credentials" → "Create Credentials" → "OAuth client
   ID" → application type "Desktop". Download the client-secret JSON.
3. Run `gcloud auth application-default login` with the appropriate Drive
   scope, OR write a small one-shot script that runs `google-auth-oauthlib`'s
   `InstalledAppFlow.run_local_server()` with scope
   `https://www.googleapis.com/auth/drive.readonly`. Save the resulting
   authorized-user JSON (`{type: "authorized_user", refresh_token: "...",
   client_id: "...", client_secret: "..."}`) to a file on the MFS server.
4. Reference it from the connector config via `credential_ref`.

### credential_ref

```toml
credential_ref = "file:/var/run/secrets/gdrive/token.json"
```

The file contents must be the **authorized-user** JSON (not service-account
JSON — Drive doesn't grant service accounts access to a user's "My Drive"
unless you go through domain-wide delegation, which is Workspace-only).

## Connector config TOML

```toml
# ─── auth (required) ───
credential_ref = "file:/var/run/secrets/gdrive/token.json"

# ─── scope (optional) ───
# root_folder_id = "0BxYzAbCdEfGhIjKl"     # restrict to a specific folder + descendants
                                            # (default = entire "My Drive")
# include_shared = true                     # also enumerate Shared Drives the user has access to
# max_files = 50000                         # safety cap on enumeration
```

No `[[objects]]` — per-file indexing follows the same extension mapping as
**file**.

## What each command does

| Command | Behaviour |
|---|---|
| `mfs ls gdrive://<alias>/<folder>/` | `files.list(q="...mimeType='application/vnd.google-apps.folder'...")` filtered by parent. |
| `mfs cat <Doc>.txt` | `files.export_media(fileId, mimeType='text/plain')` — exported on demand, cached. |
| `mfs cat <Sheet>.csv` | `files.export_media(fileId, mimeType='text/csv')`. |
| `mfs cat <binary>` | `files.get_media(fileId)` (full bytes). |
| `mfs cat <path> --range A:B` | range slice of the exported text / converted markdown. |
| `mfs head` / `tail` / `grep` | standard text operations against the exported / converted body. |
| `mfs search "QUERY"` | Milvus only. Hits land on the exported text positions, so `--range` works. |

## Typical workflow

```bash
# 1. (One-time) OAuth dance produces token.json. Drop it on the server.

# 2. Register.
cat > gdrive-mine.toml <<'EOF'
credential_ref = "file:/var/run/secrets/gdrive/token.json"
EOF
mfs add gdrive://mine --config gdrive-mine.toml

# 3. Search across docs/sheets/PDFs.
mfs search "Q1 OKR pricing tier" --connector-uri gdrive://mine
mfs cat "gdrive://mine/Engineering/RFCs/RFC-042.txt" --range 88:140

# 4. Refresh.
mfs add gdrive://mine --no-full
```

## Incremental sync (md5 / modifiedTime)

Per-file fingerprint = `md5Checksum | modifiedTime`. Drive provides
`md5Checksum` for binary uploads (PDFs, images, code) but **not for native
Google Docs/Sheets/Slides** — those use `modifiedTime` only. So editing a
Doc always re-exports + re-embeds on the next sync; editing a PDF only
re-embeds if the md5 actually changed (uploading the same bytes is a no-op).

Renames inside Drive: a file moved to a new parent gets a new path; the old
path becomes "deleted" + new path "added". No vector reuse (could be added
via Drive's `fileId` as the rename key — future).

## Gotchas

1. **OAuth not service account.** Service accounts can't read a personal
   "My Drive". For Workspace + domain-wide delegation, the auth path would
   differ — not currently supported.
2. **Shared Drives** need `include_shared = true` AND the user has to be
   a member. Otherwise they're invisible.
3. **Native Doc edits re-export every sync** — no md5 to short-circuit.
   On a fast-changing Doc this means re-embedding on every sync.
4. **`files.list` quota** — Drive caps at ~1000 requests / 100 seconds /
   user. The connector paginates conservatively. For huge Drives the
   initial sync can take a while.
5. **Permission errors on `cat`** — the OAuth user must have at least
   Viewer access on each file. Sub-folders inherit perms in Drive; private
   files under a shared folder are still inaccessible.
6. **Exported Sheets are CSV** — multi-tab sheets are flattened to the
   first sheet. For multi-tab semantics, download as `.xlsx` separately
   and re-upload to Drive as binary.
