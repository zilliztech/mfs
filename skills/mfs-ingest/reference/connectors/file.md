# file connector — ingest

URI: derived from a bare local path. `mfs add /abs/path` registers
`file://local/abs/path`. There is **no alias step** — the path is the
identity, and `mfs add` takes a single path argument (not `file://<name>`).

The everyday connector — index a local directory tree.

## Required

Just a path. No credentials, no toml in the simple case:

```bash
mfs add ./my-project           # implicit: file://local/abs/path
mfs add /var/docs              # same shape
```

## Client-side path — same vs different machine

The path is a CLIENT-side path. `mfs add <path>` decides automatically how the
server gets the bytes:

- client and server on the **same host** (loopback endpoint) → the server reads
  the path directly, no copy.
- **different hosts** → the CLI bundles and uploads the tree (no shared
  filesystem required).

Force either way with `--upload` (always bundle + upload) or `--no-upload` (server
reads the path; only valid when client and server share a filesystem).

## When you DO need a toml

For:
- `max_file_bytes` cap (skip files larger than N bytes)
- per-extension `[[objects]]` rules (e.g. don't index lock files)
- `gitignore` discipline overrides

```toml
[[objects]]
match = "*.lock"
indexable = false

[[objects]]
match = "node_modules/**"
indexable = false

max_file_bytes = 10485760    # skip >10MB files
```

```bash
mfs add ./my-project --config /tmp/mfs-file.toml
```

## URI tree

```
file://<alias>/...                 ← mirrors the on-disk tree
```

Each file becomes one object; conversion routes by media type
(`.md`/`.py`/`.go`/… as code or text, `.pdf` via markitdown,
`.png`/`.jpg` via VLM if summary is enabled).

## .gitignore + .mfsignore

The connector respects `.gitignore` AND `.mfsignore` (project-local
opt-out). Common defaults already skip `__pycache__`, `node_modules`,
`.venv`, `target/`, etc. via the native walk accelerator's built-in
rules.

## Pitfalls

- **Tree changes between adds**: the connector full-scans every sync.
  Deleted files get removed from the index, new ones added.
- **`max_file_bytes` too low**: silently skips files. Use `mfs ls
  <uri>` to see what got indexed.
- **`indexable = false` doesn't list**: those files are still
  enumerated by `mfs ls / tree`; they just don't get embedded.
  Confusing if user expects "hide from MFS entirely". Setting
  `match = "*.lock"` + `indexable = false` does exactly what they
  want for search.
- **Symlinks**: followed but tracked by absolute realpath. Circular
  symlinks would loop — the native walker breaks cycles.
