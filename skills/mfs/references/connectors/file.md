# file connector (`file://` — local paths)

Local filesystem tree under the connector root; real files keep their original
names and extensions. `mfs ls/tree ./repo` shows the real directory.

**object_kind by extension** (decides how it's indexed):
- `.md/.rst/.txt` → document; `.pdf/.docx/.pptx/.xlsx/.html` → document **auto-converted to markdown**
- code (`.py/.js/.ts/.go/.rs/.java/...`) → code (AST-aware chunking)
- images (`.png/.jpg/.gif/.webp/...`) → **VLM description** (indexed as text)
- `.json/.csv/.log/.yaml/...` → text_blob (not embedded by default; grep works)
- other → binary (metadata only)

**cat behavior**: text as-is; pdf/docx/html → converted markdown; image → VLM
description (also via `cat --meta`); binary → `<binary, N bytes>` (use `--raw`).

**workflow on a large repo**: `mfs search "<intent>" ./repo` → result has
`lines` → `mfs cat <file> --range start:end`. Use `mfs grep "<ERR_CODE>" ./repo`
for literal identifiers. `.gitignore` + `.mfsignore` are respected (ignored files
are never exposed/indexed). Rename is detected (inode/sha1) and reuses vectors.
