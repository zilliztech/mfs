# github connector — ingest

URI: `github://<owner>/<repo>` (alias derives from owner+repo).

## How to obtain credentials

A **GitHub Personal Access Token (PAT)** with read scopes.

**Fine-grained PAT (recommended for org repos)**:

1. <https://github.com/settings/tokens?type=beta> → **Generate new token**.
2. Token name + expiration (90 days default — set a reminder to rotate).
3. Repository access: **Only select repositories** → pick the ones to index.
4. Repository permissions:
   - **Contents** → Read-only (code + files)
   - **Issues** → Read-only
   - **Pull requests** → Read-only
   - **Metadata** → Read-only (always required)
5. Generate, copy `github_pat_…`.

**Classic PAT (works but org SSO friction)**:

1. <https://github.com/settings/tokens/new>.
2. Scopes: `repo` (full read of private repos) OR `public_repo` (public
   repos only).
3. For SSO-enforced orgs: after generating, click **Configure SSO** →
   authorize for the org.

## Required toml fields

| key | what |
|---|---|
| `token` | the PAT (`env:GITHUB_TOKEN` recommended; `gh auth status` users likely already have `GH_TOKEN` set) |

## Optional

| key | default | meaning |
|---|---|---|
| `repo` | (from URI) | `owner/name`; if URI is `github://owner/repo`, this is auto-derived |
| `branch` | repo default | branch to index |
| `max_read_rows` | 5000 | per object kind (issues / PRs) cap |

## URI tree

```
github://owner/repo/
├── code/                                  ← source files (tree of dirs)
├── _meta/issues.jsonl                     ← all issues (lazy NDJSON)
└── _meta/pulls.jsonl                      ← all PRs (lazy NDJSON)
```

The `code/` subtree mirrors the repo's tree; files are indexed
per-content (chunked text/code). The two `_meta/*.jsonl` objects are
record collections — one chunk per issue/PR with title + body +
comments.

## env: example

```toml
token = "env:GITHUB_TOKEN"
branch = "main"
max_read_rows = 10000
```

```bash
export GITHUB_TOKEN=ghp_... # or github_pat_...
mfs add github://zilliztech/mfs --config /tmp/mfs-gh.toml
```

## Pitfalls

- **Rate limit**: 5000 req/hour for authenticated PATs, 15000/hour for
  GitHub Apps. Big monorepos (10k+ files) can ingest slowly. The
  connector resumes after rate-limit waits.
- **SSO not authorized**: org repos return 404 (looks like "doesn't
  exist") if the PAT isn't SSO-authorized for that org. Tell user to
  visit Settings → Tokens → Configure SSO.
- **`max_read_rows` applies to issues + PRs separately**: the
  default 5000 is per-kind, so 10k total. Raise if you have more
  history.
- **Code search**: `_meta/issues.jsonl` is BM25 + dense indexed; the
  `code/` subtree files are too. Both are searchable via `mfs search`.
- **Repos with submodules**: submodule content is NOT followed.
  Index each submodule as a separate `github://` connector.
