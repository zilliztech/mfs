# github connector (`github://`)

A GitHub repository's code tree at real repo paths. `cat` returns the raw file;
`.md`/code are searchable, binaries are metadata-only. HTML/docx files route through
the framework converter to markdown.

Auth via `GITHUB_TOKEN` env (anonymous rate limit is low). Change detection uses
blob SHA per file (re-add skips unchanged blobs). `search` over code/doc chunks;
hits carry `lines` → `mfs cat <source> --range start:end`. Scope a search to the
repo URI to stay within it.

**Collaboration data under `_meta/`** (opt-in: set `index_meta=true`):
- `_meta/issues.jsonl`, `_meta/pulls.jsonl` — record_collection. Declare
  `text_fields=["title","body"]`, `locator_fields=["number"]` in `[[objects]]` to
  index; a hit's `locator` is `{number: N}` → reopen with
  `mfs cat github://<alias>/_meta/issues.jsonl --locator '{"number":42}'`.
- `_meta/pulls/<n>/diff.patch` — the PR's unified diff, indexed as a document
  (`lines` → `cat --range`).
- `max_read_rows` caps how many issues/PRs (and diff.patch objects) are pulled.
