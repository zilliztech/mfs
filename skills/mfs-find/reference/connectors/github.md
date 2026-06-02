# github connector — search & browse

## URI tree

```
github://<owner>/<repo>/
├── code/                              ← mirrors the repo's file tree
│   ├── src/main.py
│   ├── docs/README.md
│   └── ...
└── _meta/
    ├── issues.jsonl                   ← all issues (lazy NDJSON)
    └── pulls.jsonl                    ← all PRs (lazy NDJSON)
```

Code files chunked as text/code per language; issues + PRs are record
collections.

## Record shapes

**Issue (`_meta/issues.jsonl`)**:
```json
{"number": 42,
 "title": "Auth bug in SSO flow",
 "body": "When users log in via Okta...",
 "state": "open",
 "labels": [{"name": "bug"}, {"name": "auth"}],
 "author": "alice",
 "assignees": ["bob"],
 "comments": [{"author": "bob", "body": "...", "created_at": "..."}, ...],
 "updated_at": "2026-06-01T...",
 "url": "..."}
```

**PR (`_meta/pulls.jsonl`)** — same shape plus `draft`, `merged_at`,
`reviews[]`, etc.

## Chunk kinds

- **`row_text`** in `_meta/issues.jsonl` / `_meta/pulls.jsonl`: one
  per issue/PR; content combines `title + body + comments[].body`.
- **`chunk_body`** in `code/<file>`: source code chunked by AST
  (CodeChunker for Python/JS/Go/Rust/...) or by markdown headings.

## Locator

| Chunk | Locator |
|---|---|
| Issue / PR | `{"number": 42}` |
| Code file chunk | `{"lines": [start, end]}` |

```bash
mfs cat github://owner/repo/_meta/issues.jsonl --locator '{"number": 42}'
mfs cat github://owner/repo/code/src/main.py --range 100:150
```

## Search strategy

| Intent | Use |
|---|---|
| Find past tickets about X | `mfs search "X" github://owner/repo/_meta/issues.jsonl` |
| Find where Y is implemented | `mfs search "Y" github://owner/repo/code/` |
| Find PRs related to X | `mfs search "X" github://owner/repo/_meta/pulls.jsonl` |
| Cross: issues + code together | `mfs search "X" github://owner/repo` (whole repo) |

## Field semantics

- `comments[]` are flattened into chunk content (with `text_fields =
  ["title", "body", "comments[].body"]`). So search hits inside
  long-thread discussions.
- `metadata.state` ∈ {`open`, `closed`}.
- `metadata.labels[*]` is a list of label names. Search engine doesn't
  filter on this natively — apply client-side.
- `metadata.author`, `metadata.assignees[*]`, `metadata.updated_at` —
  useful filters at the application layer.

## Pitfalls

- **Issue number != PR number** in newer GitHub — they share a
  sequence. `--locator '{"number": 42}'` works for both because they
  live in different `.jsonl` files.
- **Repo with hundreds of thousands of files**: `max_read_rows` caps
  the `code/` subtree. Default 5000 issues / PRs.
- **Diff content not indexed**: PR bodies + reviews are indexed but
  the raw diff is NOT. To search inside diffs, scope to `code/` for
  the changed file content.
- **Private repos + SSO**: if a PAT works in `gh` but MFS gets 404, the
  PAT isn't SSO-authorized for the org. Fix outside MFS.
