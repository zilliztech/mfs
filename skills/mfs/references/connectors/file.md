# file connector (`file://` — local filesystem)

## What this is

The simplest and most-used connector: a local directory tree mounted as an
MFS connector. The server reads the directory directly (shared-fs mode); each
real file at `<root>/foo/bar.py` is reachable at `file://<alias>/foo/bar.py`.
Indexing decisions are made per file by **object_kind**, inferred from the
extension.

**When MFS helps**: large monorepos, docs sites, content folders with
thousands of files — you want semantic + literal search and the ability to
read a file at a precise line range. For a handful of files, plain `rg` /
`cat` is simpler.

## URI shape

The URI is the real path under the configured root:

```
file://my-repo/                              connector root
file://my-repo/src/auth/login.py             real path
file://my-repo/docs/runbook.md
```

Indexing reads files lazily — `cat` opens the real file each call (no cache
indirection beyond converted-md / VLM-description artifacts).

## Auth

None — it's a local filesystem. The MFS server needs read permission on the
root path; that's the only check.

For an upload-flow (no shared fs, the CLI uploads files into a staging area
managed by the server), the connector URI has a different shape
(`file://<client_id><root>`) and is registered automatically by `mfs add` of
a non-shared path. The same plugin handles both; auth is still just
filesystem permissions on the server side.

## Connector config TOML

The file connector usually doesn't need a config block at all — `mfs add` of
a path bootstraps it. When you want to tune behaviour, the relevant fields:

```toml
# Either supply a 'root' here, or just `mfs add ./my-repo` and let the CLI fill it in.
root = "/srv/repos/my-repo"

# Ignore patterns (gitignore-style). Combined with the repo's own .gitignore.
# An .mfsignore file at the root is also picked up automatically.
# ignore_patterns = ["node_modules/", "**/*.tmp", "build/", "!keep.log"]

# Optional: hard cap on per-file size that gets indexed (bytes). Default = no cap;
# Milvus content field is 65535 chars per chunk, so a huge file produces many chunks.
# max_file_bytes = 5_000_000
```

For most use cases:
```bash
mfs add ./my-repo                    # alias derived from path
mfs add file://docs ./docs-site      # explicit alias
```

…is all you need.

## Object kind by extension (decides how the file is indexed)

| Extension class | object_kind | Indexing |
|---|---|---|
| `.md` `.rst` `.txt` `.org` `.adoc` | `text` | direct text chunking via chonkie RecursiveChunker |
| `.pdf` `.docx` `.pptx` `.xlsx` `.html` `.htm` | `text` (via converter) | auto-converted to markdown by markitdown / framework converter, then chunked |
| `.py` `.js` `.ts` `.tsx` `.jsx` `.go` `.rs` `.java` `.c/.h/.cpp/.hpp` `.rb` `.php` `.sh` | `code` | chonkie CodeChunker (tree-sitter AST — function / class boundaries) |
| `.png` `.jpg` `.jpeg` `.webp` `.gif` `.bmp` `.tiff` | `image` | VLM (OpenAI gpt-4o-mini by default) produces a textual description → that gets embedded |
| `.csv` `.json` `.jsonl` `.yaml` `.yml` `.toml` `.log` | `binary` | not embedded; `cat` / `head` / `tail` / `grep` work as plain text |
| anything else | `binary` | metadata only; `cat` returns `<binary, N bytes>` unless `--raw` |

VLM is gated on `summary.enabled` and `summary.include_image_desc` in
server.toml. Without these, images are listed but not indexed.

## What each command does

| Command | Behaviour |
|---|---|
| `mfs ls <path>` | reads the real directory; respects `.gitignore` + `.mfsignore`. |
| `mfs tree <path>` | recursive, depth-bounded. |
| `mfs cat <file>` | text/code → raw content; pdf/docx/html → converted markdown; image → VLM description; binary → placeholder unless `--raw`. |
| `mfs cat <file> --range A:B` | byte-or-line range (line numbers when the file is text). |
| `mfs head -n N <file>` | first N lines (Rust accel reads from EOF if `tail`; standard read for `head`). |
| `mfs tail -n N <file>` | last N lines, **Rust-accelerated** (reverse-read from EOF, never loads the whole file). |
| `mfs grep "PATTERN" <path>` | Rust-accelerated linear grep (regex + literal). Walks the tree honouring gitignore. |
| `mfs search "QUERY" <path>` | Milvus hybrid search; chunks return with `locator={"lines":[start,end]}` + `chunk_kind`. Reopen via `cat --range start:end`. |
| `mfs export <path>` | streams the raw file (or converted-md for converted formats). |

## Typical workflow on a large repo

```bash
# 1. Register.
mfs add ./my-repo                       # syncs in the background; check with `mfs status`

# 2. Semantic search → confirm by reading the range.
mfs search "rate limit token bucket" --connector-uri file://my-repo --top-k 5
# Hit:  file://my-repo/src/middleware/throttle.go  lines [42, 78]
mfs cat file://my-repo/src/middleware/throttle.go --range 42:78

# 3. Literal grep for an identifier.
mfs grep "ERR_THROTTLE_EXCEEDED" file://my-repo

# 4. After local edits, refresh — rename detection avoids re-embedding.
mfs add ./my-repo --no-full
```

## Incremental sync (stat-first, sha1-confirmed, rename-aware)

`sync()` walks the tree (Rust accel via `walk_tree`, gitignore-aware), then:

1. `stat`s every file → compares `(size, mtime_ns, inode)` against the stored
   `file_state`. Unchanged → skip.
2. Files where `stat` changed get a `sha1` (Rust parallel via `sha1_files`).
   If sha1 matches stored → only mtime touched, no re-embed.
3. New `(size, sha1)` matching a deleted entry → emitted as `renamed` →
   vectors are remapped to the new path (no re-embed).

Result: a `git pull` that moves files around and edits a handful re-embeds
**only the changed files**, not the renamed ones.

`.gitignore` and `.mfsignore` are read once per sync; updates to either take
effect on the next sync.

## Gotchas

1. **`.gitignore` is honoured** — newly ignored files become "deleted" on
   next sync (vectors purged). To override locally without touching the
   repo's `.gitignore`, drop an `.mfsignore` at the root with negations
   (`!important.log`).
2. **Image VLM costs money** — every new image triggers a VLM call. Set
   `summary.include_image_desc = false` in `server.toml` if you don't want
   image indexing, or filter image extensions via `.mfsignore`.
3. **Auto-conversion of pdf/docx/html** happens once and is cached in the
   transformation cache. Re-`cat` is fast; the first read takes seconds.
4. **No live `watch`** in this version — sync is invoked explicitly
   (`mfs add --no-full` re-syncs). Capability flag `watch=false`.
5. **Symlinks**: followed by default. If you don't want that, list them in
   `.mfsignore`.
6. **Huge files**: a single file with 100k lines becomes many chunks
   (RecursiveChunker default `chunk_size` is in `server.toml`). For
   pathological inputs (auto-generated logs, minified bundles), add the
   extension to `.mfsignore` or set `max_file_bytes` in the connector
   config.
