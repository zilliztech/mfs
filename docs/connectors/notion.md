# Notion (`notion`)

The `notion` connector indexes Notion pages as markdown documents and Notion
database (data source) entries as searchable records.

## How MFS sees it

```text
notion://workspace/
├── pages/
│   └── <page-id>.md                       document
└── data_sources/
    └── <data-source-id>/
        ├── records.jsonl                  record_collection
        └── schema.json                    table_schema
```

Pages render to markdown and are searchable with no extra config. Data source
records need `[[objects]].text_fields` to become searchable (their properties are
flattened before rendering).

## Credentials

You need a **Notion Internal Integration token** plus explicit page sharing.

1. <https://www.notion.so/profile/integrations> → *New integration* → pick the
   workspace.
2. Under *Capabilities*, enable **Read content** (nothing else is needed).
3. Copy the **Internal Integration Token** (`secret_…`).

The token alone grants nothing — you must **share** each page or database with the
integration: open the page → `•••` → *Connections* → *Connect to* → the
integration. Sharing propagates to sub-pages, so sharing the workspace home page
is the workspace-wide path; sharing one section scopes it there.

## Configuration

```toml
token = "env:NOTION_TOKEN"

[[objects]]
match = "/data_sources/<data-source-id>"
text_fields = ["Name", "Description"]
locator_fields = ["id"]
```

The `[[objects]]` block is only needed for searchable database records — pages
work without it.

## Sync and freshness

The connector uses each page's `last_edited_time` as its cursor for incremental
re-sync; deletions are caught by `full_scan`.

## Search and browse

```bash
mfs add notion://workspace --config ./notion.toml

mfs search "launch checklist" notion://workspace/pages
mfs cat notion://workspace/pages/<page-id>.md --range 1:80
mfs cat notion://workspace/data_sources/<data-source-id>/records.jsonl --locator '{"id":"<page-id>"}'
```

## Pitfalls

- The integration only sees pages and databases explicitly shared with it.
- The plugin uses Notion's `data_source` API and paths under `data_sources/`
  (older terminology said "databases").
- Database records need `text_fields` to be searchable.
