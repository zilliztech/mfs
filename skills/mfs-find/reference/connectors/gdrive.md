# gdrive connector — search & browse

## URI tree

```
gdrive://<alias>/
└── <folder>/<file>.<ext>
```

Tree shape depends on what's been shared with the credential. Each
file becomes one MFS object.

## File conversion

| Source | Result |
|---|---|
| Google Doc | exported as plain text |
| Google Sheet | exported as CSV (then parsed as table-like) |
| Google Slide | exported as plain text per slide |
| PDF | markitdown → markdown |
| Office files (`.docx`/`.xlsx`/`.pptx`) | markitdown |
| Plain text / markdown / code | indexed as-is |
| Images | VLM description (if enabled) |

## Chunk kinds

- `chunk_body` for text/markdown/code/converted docs
- `vlm_description` for images (only with VLM)
- `directory_summary` for dirs (only with summary)

## Locator

`{"lines": [s, e]}` for text chunks; `mfs cat <uri> --range A:B` to
slice.

## Search strategy

| Intent | Use |
|---|---|
| "Where did we write about X" | `mfs search "X" gdrive://<alias>` |
| Restrict to a folder | `mfs search "X" gdrive://<alias>/<folder-path>/` |
| Get original file | `mfs export gdrive://<alias>/<path> /tmp/copy.ext` |

## Pitfalls

- **Shortcut files**: a Drive "shortcut" is a symlink, not the
  content. The connector follows shortcuts but the target's
  permissions still apply.
- **Workspace export quotas**: exporting Google native files (Docs,
  Sheets, Slides) counts against the user's daily quota. Large drives
  may hit it.
- **Comments NOT indexed**: only document body. Comment threads
  attached to Google Docs are invisible.
- **Owner vs editor**: the credential sees only what's shared with
  it (service account) or what the user can see (OAuth). "Where's
  doc X?" → confirm sharing first.
