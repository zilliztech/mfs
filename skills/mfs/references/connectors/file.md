# file connector (`file://` — local filesystem)

## Contents

- [What this is — and the two operating modes](#what-this-is--and-the-two-operating-modes) — shared-fs vs client-server upload, when each applies
- [URI shape](#uri-shape) — what `file://...` looks like in each mode, why the alias matters
- [Authentication](#authentication) — none, but read permission matters; CS mode uses `client_id`
- [Connector config TOML](#connector-config-toml) — minimal fields, when to set `root` / `ignore_patterns` / `max_file_bytes`
- [Object kind by extension](#object-kind-by-extension-decides-indexing) — what gets `text` / `code` / `image` / `binary` treatment
- [What each command does](#what-each-command-does) — per-command behaviour on a file source
- [Typical workflow on a large repo](#typical-workflow-on-a-large-repo) — end-to-end example
- [Incremental sync](#incremental-sync--how-rename-detection-and-stat-first-hashing-work) — file_state table, rename detection, why no checkpoint, what `--full` does
- [Indexing priority](#indexing-priority--first-screen-visible-files-index-first) — README / src / app land first, tests / vendor later
- [Ignore rules](#ignore-rules--mfsignore-gitignore-and-defaults) — three-layer override (`.mfsignore` > `.gitignore` > built-in defaults)
- [Gotchas](#gotchas) — symlinks, huge files, no `watch`, no-extension files, VLM cost

## What this is — and the two operating modes

A local directory tree mounted as an MFS connector. The MOST-used connector;
also the most subtle, because there are **two operating modes** that look
identical to the user but use different transport mechanics under the hood.
The same plugin code handles both — only the path-to-server transport
differs.

| Mode | When | What happens |
|---|---|---|
| **Shared-filesystem (`shared-fs`)** | local profile, OR remote profile with the same `machine-id` as the server (server can see the same disk) | server reads `<root>/foo/bar.py` directly from disk; nothing is uploaded |
| **Client-server upload (`cs`)** | remote profile, different machine — the server cannot see your disk | CLI bundles changed bytes into a multipart upload (2 HTTP RTTs), server stores them in a per-client staging area and indexes from there |

The CLI auto-detects mode at `mfs add` time by comparing `machine-id` over
the handshake:

| Server endpoint | machine-id matches? | Mode |
|---|---|---|
| local socket / `127.0.0.1` | n/a | shared-fs |
| remote HTTPS, same machine-id | ✓ | shared-fs (server reads directly) |
| remote HTTPS, different machine-id | ✗ | CS (CLI uploads) |

Both modes converge in the connector: the file plugin always scans a "real
directory" it can `stat()` — shared-fs scope points at your repo, CS scope
points at the server-side staging area. **file_state table semantics,
chunking, embedding, search — all identical**.

Force the mode if needed:

```bash
mfs add ./repo --no-upload     # force shared-fs (server reads directly)
mfs add ./repo --upload        # force CS (bundle + upload)
```

Forcing `--no-upload` against a server that genuinely can't see your disk
will fail with "path not accessible" — that's the safety net.

## URI shape

The URI internally records which root the path belongs to. Users mostly
write plain paths; the framework normalises.

```bash
# What the user writes
mfs add ./my-repo
mfs add file://docs ./docs-site

# What gets stored internally
# Shared-fs mode:  file://<alias>/<rel-path>
file://my-repo/src/auth/login.py
file://docs/getting-started.md

# CS mode:  file://<client_id>/<abs-path>
# (client_id is a UUIDv7 generated on CLI first-run, kept in ~/.mfs/client.toml)
file://01939c8b-a4d1-7f12-9c0a-3e7d4f8a/abs/path/to/my-repo/src/auth/login.py
```

The `<client_id>` form is **the stable identifier across CLI restarts**: as
long as the user keeps `~/.mfs/client.toml`, re-running `mfs add ./repo`
from a fresh shell maps to the same connector. Lose `client.toml` → the
file source looks "new" to the server (and re-sync uploads everything).

Indexing reads files lazily — `cat` opens the real file each call (no cache
indirection beyond `converted_md` / `image_vlm_description` artifacts).

## Authentication

None — it's a filesystem. The MFS server needs read permission on the root
path (shared-fs) or write permission on the staging area (CS); both are
deploy-time concerns, no per-source token.

In CS mode the implicit "credential" is the `client_id` — a different
machine with a different `client_id` cannot read or modify another
client's staged files. The HTTP layer's auth (profile API token) gates the
upload itself.

## Connector config TOML

The file connector usually doesn't need a config block at all — `mfs add`
of a path bootstraps it. Tunable fields:

```toml
# Either supply 'root' here, or pass it as the mfs add argument.
root = "/srv/repos/my-repo"

# Ignore patterns (gitignore-style). Combined with the repo's own .gitignore
# and an .mfsignore file at the root if present. See "Ignore rules" below.
# ignore_patterns = ["node_modules/", "**/*.tmp", "build/", "!keep.log"]

# Hard cap on per-file size that gets indexed (bytes). Default = no cap;
# Milvus content field is 65535 chars per chunk, so a huge file produces
# many chunks. Ask the user before raising for log/minified inputs.
# max_file_bytes = 5_000_000

# Force a specific mode (rarely needed; auto-detect is correct in 99% of cases).
# mode = "shared_fs"   # or "cs"
```

For most use cases the TOML is empty:

```bash
mfs add ./my-repo                    # alias derived from path
mfs add file://docs ./docs-site      # explicit alias
```

## Object kind by extension (decides indexing)

| Extension class | object_kind | Indexing |
|---|---|---|
| `.md` `.rst` `.txt` `.org` `.adoc` | `text` | direct text chunking via chonkie RecursiveChunker |
| `.pdf` `.docx` `.pptx` `.xlsx` `.html` `.htm` | `text` (via converter) | auto-converted to markdown by markitdown / framework converter, then chunked |
| `.py` `.js` `.ts` `.tsx` `.jsx` `.go` `.rs` `.java` `.c/.h/.cpp/.hpp` `.rb` `.php` `.sh` | `code` | chonkie CodeChunker (tree-sitter AST — function / class boundaries) |
| `.png` `.jpg` `.jpeg` `.webp` `.gif` `.bmp` `.tiff` | `image` | VLM (OpenAI gpt-4o-mini by default) produces a textual description → that gets embedded |
| `.csv` `.json` `.jsonl` `.yaml` `.yml` `.toml` `.log` | `binary` | not embedded; `cat` / `head` / `tail` / `grep` work as plain text |
| **no extension** (`README`, `Makefile`, `LICENSE`, `Dockerfile`) | `binary` | classified as binary because the extension table is the sole signal — `cat` returns the text fine but `search` will not find it. Add `.md` or list as text via custom plugin if you want search recall. |
| anything else | `binary` | metadata only; `cat` returns `<binary, N bytes>` unless `--raw` |

VLM is gated on `summary.enabled` and `summary.include_image_desc` in
`server.toml`. Without these, images are listed but not indexed.

## What each command does

| Command | Behaviour |
|---|---|
| `mfs ls <path>` | reads the real directory; respects `.gitignore` + `.mfsignore` + defaults. |
| `mfs tree <path>` | recursive, depth-bounded. |
| `mfs cat <file>` | text/code → raw content; pdf/docx/html → converted markdown; image → VLM description; binary → placeholder unless `--raw`. |
| `mfs cat <file> --range A:B` | byte-or-line range (line numbers when the file is text). |
| `mfs head -n N <file>` | first N lines. |
| `mfs tail -n N <file>` | last N lines, **Rust-accelerated** (reverse-read from EOF, never loads the whole file). |
| `mfs grep "PATTERN" <path>` | Rust-accelerated linear grep (regex + literal). Walks the tree honouring ignore rules. |
| `mfs search "QUERY" <path>` | Milvus hybrid search; chunks return with `locator={"lines":[start,end]}` + `chunk_kind`. Reopen via `cat --range start:end`. |
| `mfs export <path>` | streams the raw file (or converted-md for converted formats). |

## Typical workflow on a large repo

```bash
# 1. Register. Auto-detects shared-fs vs CS; returns a job id.
mfs add ./my-repo
mfs status                            # poll until 'available'

# 2. Semantic search → confirm by reading the range.
mfs search "rate limit token bucket" --connector-uri file://my-repo --top-k 5
# Hit:  file://my-repo/src/middleware/throttle.go  lines [42, 78]
mfs cat file://my-repo/src/middleware/throttle.go --range 42:78

# 3. Literal grep for an identifier.
mfs grep "ERR_THROTTLE_EXCEEDED" file://my-repo

# 4. After local edits, refresh — rename detection avoids re-embedding.
mfs add ./my-repo
```

## Incremental sync — how rename detection and stat-first hashing work

`sync()` walks the tree (Rust accel via `walk_tree`, ignore-aware), then
compares reality against the server-side **`file_state`** table. Behaviour
identical in both modes — only the source of "reality" differs (server
reads disk directly vs server reads staging area populated by upload).

### Stat-first lazy hashing

For each path the connector sees:

1. `stat()` → compare `(size, mtime_ns)` against `file_state`.
2. **Unchanged** → skip. No read, no hash, no embed cost.
3. **`stat` differs** → compute `sha1` (Rust parallel via `sha1_files`).
   - **`sha1` matches** → only mtime touched; UPDATE `mtime_ns` in
     `file_state`, skip embedding.
   - **`sha1` differs** → genuinely modified; re-chunk + re-embed.
4. Paths in `file_state` but not in current scan → candidates for `deleted`
   (subject to rename pairing below).
5. Paths in current scan but not in `file_state` → candidates for `added`
   (subject to rename pairing).

This is why a `git pull` that touches mtimes on hundreds of files but only
changes a handful re-embeds **only the genuinely changed ones**.

### Rename detection — inode pairing + sha1 fallback

When a path disappears and a new path appears in the same sync, the
connector tries to pair them so vectors get remapped (chunk_id rewritten,
no re-embed):

1. **Same inode + same size** (typical `mv` on the same filesystem) →
   pair instantly, zero sha1 cost.
2. **Different inode but same `(size, sha1)`** → pair via sha1 (covers
   `cp + rm`, cross-fs moves, or filesystems where inodes lie across
   reboots).
3. **CS mode pre-pairing** — the CLI's manifest step can pair on inode
   before the upload (saving bandwidth). The server-side post-walk repeats
   the sha1 pairing as a fallback in case the CLI missed it.

The `cross_fs_inode` flag in `file_state` records whether inode numbers
are trustworthy on this deployment — if mismatched at startup (e.g. the
server moved disks), the connector treats inodes as NULL and falls back
to sha1 pairing for renames.

### Why no checkpoint, and what `--full` does

`file_state` is a **snapshot of the entire tree** — half a snapshot is not
a legal state ("the rest is `deleted`" would be wrong). So the file
connector does **NOT** call `self.state.checkpoint()` mid-sync, unlike
cursor-based connectors (postgres, slack, github) that can safely
checkpoint partway. A killed file sync rolls back to the previous full
snapshot.

`--full` forces every path to re-hash + re-embed even when `stat`
agrees, useful when:

- you suspect mtime got rewritten without content change (some VCS
  operations, restored backups);
- the chunker / embedder version changed and you want to refresh
  representations (v0.4 doesn't track these in `file_state`);
- diagnosis says vectors are stale and you've exhausted §6 of SKILL.md.

`--full` costs the full embedding budget. ASK the user before running it
on a multi-thousand-file source.

## Indexing priority — first-screen-visible files index first

The file connector overrides `task_priority()` so a freshly-added repo
gets its most-asked-about files indexed first (lower number = sooner):

| File pattern | Relative priority |
|---|---|
| `README*`, `pyproject.toml`, `package.json`, `Cargo.toml` | high (early) |
| `src/`, `lib/`, `app/`, top-level source dirs | high (early) |
| top-level docs / RFC paths | mid |
| `tests/`, `test/`, `__tests__/` | low (late) |
| `dist/`, `build/`, `vendor/`, generated output (still indexed if not in `.mfsignore`) | lowest (latest) |

Implication for the agent: while `mfs status <uri>` says `building`, the
core files are usually already searchable. `mfs ls <uri> --json` shows
per-object `search_status` — a quick way to confirm the README hit the
index before the tests.

Priority only affects scheduling order, not correctness — chunk_id is
deterministic regardless of order, so the final index is identical.

## Ignore rules — `.mfsignore`, `.gitignore`, and defaults

Three layers, evaluated together. Higher in this list = higher precedence:

1. **`.mfsignore`** at the connector root — MFS-specific, gitignore syntax,
   supports `!pattern` negations. Use to override the other two layers
   (re-include a generated dir, exclude a sensitive path `.gitignore`
   doesn't cover).
2. **`.gitignore`** in the repo — picked up as-is. **Newly ignored files
   become "deleted" on next sync** (vectors purged) — that's the trap
   when editing `.gitignore` mid-project. To override locally without
   touching `.gitignore`, use `.mfsignore` with a `!pattern`.
3. **Built-in defaults** — common bulk noise that the connector hard-codes:
   `node_modules/`, `dist/`, `build/`, `.venv/`, `__pycache__/`, `.git/`,
   `.idea/`, `.vscode/`. Override via `.mfsignore` if you genuinely need
   one of these indexed.

All three are read once per sync; updates take effect on the next sync.

## Gotchas

1. **No-extension files (`README`, `Makefile`, `LICENSE`)** — classified
   as `binary` per the extension table, so `mfs cat` reads them fine but
   `mfs search` won't surface them. Rename to `README.md` for search
   recall, or accept that you'll find them via `ls` / `grep` only.
2. **Image VLM costs money** — every new image triggers a VLM call. Set
   `summary.include_image_desc = false` in `server.toml` to skip image
   indexing, or filter image extensions via `.mfsignore`.
3. **Auto-conversion of pdf/docx/html** happens once and is cached as a
   `converted_md` artifact in the transformation cache. Re-`cat` is fast;
   the first read takes seconds.
4. **No live `watch`** in this version — sync is invoked explicitly
   (`mfs add` re-syncs incrementally). Capability flag `watch=false`.
5. **Symlinks**: followed by default. If you don't want that, list them
   in `.mfsignore`.
6. **Huge files**: a single 100k-line file becomes many chunks
   (RecursiveChunker default `chunk_size` is in `server.toml`). For
   pathological inputs (auto-generated logs, minified bundles), add the
   extension to `.mfsignore` or set `max_file_bytes` in the connector
   config.
7. **CS mode and client_id loss**: if `~/.mfs/client.toml` is deleted,
   the new CLI gets a fresh UUIDv7 and the server can't reconcile the
   old paths — they look orphaned. Treat the previous source as
   un-indexed (the orphaned rows get reaped on the server's next
   garbage-collect pass). To keep identity across reinstalls, back up
   `client.toml`.
8. **Half-committed sync is illegal here** — see "Why no checkpoint"
   above. Don't kill `mfs add` halfway expecting it to resume; it
   restarts from the last full snapshot.
