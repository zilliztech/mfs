# github connector (`github://`)

## What this is

A GitHub repository exposed as a filesystem tree at the repo's real paths,
plus an opt-in `_meta/` subtree for issues / pull requests / per-PR diffs.
Uses the GitHub REST API (`httpx`), no SDK — the API surface is small.

**When MFS helps**:
- a repo too big to clone (or not yet cloned) but you want to search it
- you want unified search across **code + issues + PR conversations** —
  e.g. "where do we configure SSL verification" should find both
  `src/http/client.go` and the PR discussion that flipped the default.

**When file connector beats this**: the repo is already cloned locally
and you have working-tree edits. The github connector reads from a specific
branch via the API — it doesn't see uncommitted changes.

## URI shape

```
github://<alias>/                                  connector root (one repo)
github://<alias>/src/auth/login.go                 real file at repo path
github://<alias>/docs/runbook.md
github://<alias>/_meta/                            collaboration subtree (opt-in)
github://<alias>/_meta/issues.jsonl                all issues as a record stream
github://<alias>/_meta/pulls.jsonl                 all pull requests
github://<alias>/_meta/pulls/42/diff.patch         one PR's unified diff
```

Code-tree paths come from `git/trees?recursive=1` for the configured branch
(default = repo's default branch). File contents are fetched from
`raw.githubusercontent.com` lazily.

## Auth

```toml
credential_ref = "env:GITHUB_TOKEN"      # personal access token or fine-grained token
```

Anonymous requests work (and don't need `credential_ref`) but the rate
limit is 60 req/hour — fine for a tiny repo, useless for anything real.
A token raises the limit to 5000 req/hour. For private repos a token is
required (with `repo` scope or, for fine-grained tokens, `Contents: Read` +
optionally `Issues: Read` / `Pull requests: Read`).

Where to create: github.com → Settings → Developer settings → Personal access
tokens. **Fine-grained** is recommended (scope by repo + permission).

## Connector config TOML

```toml
# ─── repo (required) ───
repo = "anthropics/mfs"                  # "owner/name"
credential_ref = "env:GITHUB_TOKEN"

# ─── optional ───
# branch       = "main"                  # default = repo's default branch
# endpoint     = "https://api.github.com" # change for GitHub Enterprise
# index_meta   = true                    # opt-in to _meta/ (issues / PRs / diffs)
# max_read_rows = 5000                   # cap on issues/PRs/diff.patch objects per sync

# ─── collaboration data: needs explicit field mapping to be searchable ───
# (only when index_meta = true; the code tree doesn't need [[objects]])
[[objects]]
match           = "/_meta/issues.jsonl"
text_fields     = ["title", "body", "comments[].body"]
metadata_fields = ["state", "labels[*]", "author", "assignees[*]", "updated_at"]
locator_fields  = ["number"]

[[objects]]
match           = "/_meta/pulls.jsonl"
text_fields     = ["title", "body", "reviews[].body", "comments[].body"]
metadata_fields = ["state", "draft", "labels[*]", "author", "merged_at", "updated_at"]
locator_fields  = ["number"]
```

A built-in **preset** named `github.issues` / `github.pulls` is applied
automatically if you don't write your own `[[objects]]` for them — so the
config above is mostly informational; the defaults are the same shape.

## What each command does

| Command | Behaviour |
|---|---|
| `mfs ls /` | top-level repo tree (folders + files). |
| `mfs tree /` | recursive tree from `git/trees?recursive=1`. |
| `mfs cat <file>` | `GET raw.githubusercontent.com/<owner>/<repo>/<branch>/<path>` (cached on first read). |
| `mfs cat <file> --range A:B` | line range from the cached text. |
| `mfs grep "PATTERN" /` | linear grep over all text/code files (downloads on demand). |
| `mfs search "QUERY"` | Milvus hybrid; hits in code → `{path, lines}`; hits in issues/PRs → `{path: /_meta/.../jsonl, locator: {number: N}}`. |
| `mfs cat /_meta/issues.jsonl --locator '{"number":42}'` | refetches the single issue's full JSON. |
| `mfs cat /_meta/pulls/42/diff.patch` | the PR's unified diff. |

## Typical workflow

```bash
# 1. Create a fine-grained token, scope to the target repo, set:
export GITHUB_TOKEN=ghp_xxx

# 2. Register; turn on _meta to also get issues + PRs.
cat > my-repo.toml <<'EOF'
repo = "anthropics/mfs"
credential_ref = "env:GITHUB_TOKEN"
index_meta = true
EOF
mfs add github://mfs --config my-repo.toml

# 3. Code search.
mfs search "rate limit token bucket" --connector-uri github://mfs --top-k 5
mfs cat github://mfs/src/middleware/throttle.go --range 42:78

# 4. Issue / PR search.
mfs search "we should switch to bearer tokens" --connector-uri github://mfs
# hit might be:  github://mfs/_meta/pulls.jsonl  locator: {"number": 137}
mfs cat github://mfs/_meta/pulls.jsonl --locator '{"number":137}'
mfs cat github://mfs/_meta/pulls/137/diff.patch

# 5. Re-sync (blob SHA per file → unchanged blobs are skipped).
mfs add github://mfs --no-full
```

## Incremental sync

Code tree: per-file fingerprint = the **blob SHA** from
`git/trees?recursive=1`. Renames/edits in upstream change the blob SHA →
fetched + re-embedded; unchanged files cost zero work.

Issues / PRs: per-object fingerprint = `updated_at`. The connector pages
issues / PRs ordered by `updated`, descending, breaking once it hits a
fingerprint it has already seen.

Diff patches: one per PR; refreshed when the PR's `updated_at` changes.

## Gotchas

1. **`index_meta` is off by default.** Without it, only the code tree is
   indexed — issues / PRs / diffs are invisible. Turn it on if you want
   unified code + discussion search.
2. **Anonymous rate limit is severe** (60 req/h). Always set a token in
   production.
3. **GitHub Enterprise**: set `endpoint = "https://github.acme.com/api/v3"`.
   Same auth model.
4. **Huge repos**: the code tree fetch is one big tree call. For monorepos
   with hundreds of thousands of files, the initial sync can take minutes.
5. **`max_read_rows` caps issue/PR/diff scans.** A repo with 50000+
   issues won't all index in one go — bump the cap or paginate manually
   via multiple connectors scoped by label.
6. **No webhook live mode** — refresh is via `mfs add --no-full`.
7. **Private repo file contents** require the token to have the right
   scope (fine-grained: `Contents: Read`). A token good for issues but
   not contents will list files but fail on `cat`.
