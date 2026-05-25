# github connector (`github://`)

A GitHub repository's code tree at real repo paths (issues/pulls under `_meta/`
planned). `cat` returns the raw file; `.md`/code are searchable, binaries are
metadata-only. HTML/docx files route through the framework converter to markdown.

Auth via `GITHUB_TOKEN` env (anonymous rate limit is low). Change detection uses
blob SHA per file (re-add skips unchanged blobs). `search` over code/doc chunks;
hits carry `lines` → `mfs cat <source> --range start:end`. Scope a search to the
repo URI to stay within it.
