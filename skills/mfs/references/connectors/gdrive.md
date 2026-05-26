# gdrive connector (`gdrive://` — Google Drive)

Mirrors the Drive file tree, files at their folder paths. Native Google types are
exported on read; regular files keep their bytes.

- Google **Doc** → `<name>.txt` (exported text) → object_kind `document`
- Google **Sheet** → `<name>.csv` → object_kind `text_blob`
- Google **Slides** → `<name>.txt` → `document`
- uploaded `.pdf/.docx` → `document` (auto-converted to markdown when indexed)
- images → VLM description; other files by extension (same mapping as file connector)

**cat** a Doc/Sheet returns the exported text/CSV; a binary file returns bytes
(`--range`/`export` for large). **search** → hit `lines` → `mfs cat <path> --range a:b`.

Auth: OAuth user credentials (`token` in config, authorized-user JSON). Fingerprint
via `md5Checksum`/`modifiedTime`. Folder structure is reconstructed from Drive parents.
