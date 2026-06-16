# GitHub (`github`)

The `github` connector indexes a GitHub repository: its source files at their
repo paths, and — when you opt in — its issues, pull requests, and PR diffs. Use
it to search a codebase you don't have checked out, or to bring issue and review
discussion into the same search surface as the code it's about.

## How MFS sees it

Repository files appear at their real repo paths. With `index_meta = true`,
issue and PR records show up under `_meta/`:

```text
github://zilliztech/mfs/
├── README.md                              document
├── server/python/src/.../registry.py      code
├── _meta/issues.jsonl                     record_collection  (opt-in)
└── _meta/pulls.jsonl                      record_collection  (opt-in)
```

Code and docs are embedded and searchable like any file tree. Issues and pulls
are indexed per record using built-in presets — `github.issues` covers the title,
body, and comment bodies; `github.pulls` adds review and comment threads — so you
don't need to write any `[[objects]]` config for them.

## Credentials

You need a **GitHub Personal Access Token (PAT)** with read scopes. A
fine-grained PAT is the recommended path for org-owned repos.

**Fine-grained PAT** — <https://github.com/settings/tokens?type=beta> → *Generate
new token*:

1. Set a name and expiration (rotate on a reminder).
2. Repository access → *Only select repositories* → pick the repos to index.
3. Repository permissions, all Read-only: **Contents**, **Issues**,
   **Pull requests**, and **Metadata** (Metadata is always required).
4. Generate and copy the `github_pat_...` value.

**Classic PAT** also works (<https://github.com/settings/tokens/new>) with scope
`repo` for private repos or `public_repo` for public-only. If the org enforces
SSO, click *Configure SSO* on the token and authorize it for the org.

Author the token as an `env:` reference so the secret stays in the server
environment, never in the TOML.

## Configuration

```toml
repo = "zilliztech/mfs"
branch = "main"            # empty = repo default branch
token = "env:GITHUB_TOKEN"
index_meta = true          # also index issues + PRs (off by default)
max_read_rows = 5000
```

Set `repo` explicitly — the plugin reads it from config rather than deriving it
from the URI. `file:/abs/path` works for the token too.

## Sync and freshness

The connector tracks each file's `blob_sha` as its cursor, so a re-sync only
re-embeds blobs whose content changed. Deletions are caught by `full_scan`: a
file removed upstream disappears from the index on the next sync. Submodules are
not followed as separate repository trees.

## Search and browse

```bash
mfs connector probe github://zilliztech/mfs --config ./github.toml
mfs add github://zilliztech/mfs --config ./github.toml

mfs search "connector registry" github://zilliztech/mfs/server/python
mfs cat github://zilliztech/mfs/server/python/src/mfs_server/connectors/registry.py --range 1:80
mfs cat github://zilliztech/mfs/_meta/issues.jsonl --locator '{"number":42}'
```

## Pitfalls

- Issues, PRs, and PR diffs are **opt-in** — set `index_meta = true`.
- Private repos require a `token`; without one only public repos resolve.
- `max_read_rows` caps files and issues; a large repo may report partial recall.
