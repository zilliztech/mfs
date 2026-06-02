# notion connector — ingest

URI: `notion://<alias>`.

## How to obtain credentials

A **Notion Internal Integration token** with workspace access:

1. <https://www.notion.so/profile/integrations> → **New integration**.
2. Pick a name + workspace.
3. Capabilities → tick `Read content` (others not needed for MFS).
4. Submit → copy the **Internal Integration Token** (`secret_...`).

The token alone doesn't grant access to any pages — you have to
**share** each page/database with the integration:

- Open the top-level page → top-right `•••` → **Connections** →
  **Connect to** → pick the integration.
- Sharing propagates to all sub-pages (it's a tree-rooted permission).

For workspace-wide visibility, share the workspace's "home" page; for
scoped visibility, share just the docs/section you want indexed.

## Required toml fields

| key | what |
|---|---|
| `token` | the `secret_…` token (`env:NOTION_TOKEN` recommended) |

No `[[objects]]` needed — Notion pages auto-render to text via the
connector's block walker.

## URI tree

```
notion://<alias>/
├── databases/
│   ├── <db-title>__<db-id>/rows.jsonl     ← each DB row as a record
│   └── ...
└── pages/
    ├── <page-title>__<page-id>.md         ← each top-level shared page
    └── ...
```

The exact subtree depends on what's been shared with the integration.

## env: example

```toml
token = "env:NOTION_TOKEN"
```

```bash
export NOTION_TOKEN=secret_...
mfs add notion://acme --config /tmp/mfs-notion.toml
```

## Pitfalls

- **Sharing is silent**: if the user expected page X to show up but it
  doesn't, the integration was never connected to it. Have the user
  re-do the `Connections` → `Connect to` step on that page.
- **Block API rate limit**: ~3 req/sec per integration. Large
  workspaces (1000+ pages) ingest slowly.
- **Database property serialization**: Notion DB rows have typed
  properties (rich_text, select, multi_select, person, …). The
  connector flattens these to plain strings; complex types (e.g.
  "person mentioned in body") become the user's name only.
- **Page snapshot vs live**: indexed content is the snapshot at sync
  time. Edits show up after the next sync.
