# notion connector — search & browse

## URI tree

```
notion://<alias>/
├── databases/
│   ├── <db-title>__<db-id>/rows.jsonl
│   └── ...
└── pages/
    ├── <page-title>__<page-id>.md
    └── ...
```

The tree depends on what the integration has been shared with. New
page or database sharing requires a re-sync to appear.

## Record / page shape

**Database row** (`<db>/rows.jsonl`):
```json
{"id": "<page-id>",
 "title": "...",
 "<property-name>": "<flattened value>",
 ...
}
```

Notion property types (rich_text, select, multi_select, person,
relation, date, …) get flattened to strings.

**Page** (`pages/<title>__<id>.md`):
A markdown file rendered from the Notion page's block tree. Headings,
lists, code blocks preserved; mentions and inline DBs flatten.

## Chunk kinds

- **`row_text`** for database rows
- **`chunk_body`** for pages (recursive chunker)

## Locator

| Chunk | Locator |
|---|---|
| DB row | `{"id": "<page-id>"}` |
| Page chunk | `{"lines": [s, e]}` |

## Search strategy

| Intent | Use |
|---|---|
| Find DB row | `mfs search "X" notion://<alias>/databases/` |
| Find page content | `mfs search "X" notion://<alias>/pages/` |
| Workspace-wide | `mfs search "X" notion://<alias>` |

## Pitfalls

- **Empty tree**: integration not shared with anything. The
  `databases/` and `pages/` dirs only show what the integration can
  access.
- **Property flattening loses type**: a `select` property of value
  "High" appears as the string `"High"` in chunk content; no way to
  distinguish from a `rich_text` field with the same content.
- **Linked databases**: Notion's "Linked DB" blocks render as
  references in the page markdown but the linked DB itself appears
  separately under `databases/` if shared.
- **Stale content**: page edits show up after the next sync.
