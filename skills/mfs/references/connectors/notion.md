# notion connector (`notion://`)

## What this is

Notion workspace — pages + databases. Uses `notion-client` `AsyncClient`.
Pages are rendered to markdown by walking the block tree; databases become
record streams of rows.

**When MFS helps**: a Notion workspace with hundreds of pages + databases —
RFCs, runbooks, meeting notes, plus operational databases (project tracker,
on-call rota). Unified search across pages + db rows is otherwise a
non-trivial workflow.

## URI shape

Two kinds of objects:

```
# Pages (object_kind = text, indexed as document)
notion://<alias>/pages/<page-id>.md                rendered markdown

# Databases (object_kind = record_collection)
notion://<alias>/data_sources/<data-source-id>/records.jsonl   lazy rows
notion://<alias>/data_sources/<data-source-id>/schema.json     property list
```

Page IDs and database IDs are the Notion UUIDs (`xxxxxxxx-xxxx-...`). The
URL form `<title>-<id>` is common in Notion's UI; the connector uses just
the ID.

## Auth — Integration Token

Notion API auth is via an **internal integration token** scoped to the
workspace. **Critical**: each page / database you want indexed must be
explicitly **shared into the integration** from Notion's UI — the token
alone gives no access.

```toml
credential_ref = "env:NOTION_TOKEN"        # value: "ntn_xxx..." (or older "secret_xxx...")
```

Where to create:
1. notion.so/my-integrations → "+ New integration" → fill in name + select
   workspace → Submit. Copy the "Internal Integration Secret".
2. In Notion, navigate to the top-level page(s) / database(s) you want
   indexed → "..." menu → "Add connections" → select your integration.
   This grants the integration read access to that page and its descendants.

Without step 2 the API responds with empty pages and empty databases —
silently. There's no "list everything in the workspace" path.

## Connector config TOML

```toml
# ─── auth (required) ───
credential_ref = "env:NOTION_TOKEN"

# ─── optional ───
# max_read_rows = 5000                # per database; default 1000

# Pages don't need [[objects]] — rendered as documents.

# Databases need [[objects]] for searchability:
[[objects]]
match           = "/data_sources/*/records.jsonl"
text_fields     = ["Name", "Description"]     # title + rich-text properties usually
metadata_fields = ["Status", "Priority", "Owner"]
locator_fields  = ["id"]                       # always the Notion page-id of the row
```

The connector flattens Notion property types into the record dict:

| Property type | Flattened form |
|---|---|
| `title` / `rich_text` | concatenated plain text |
| `select` | option name (string) |
| `multi_select` | list of option names |
| `people` | list of names |
| `date` | `start` ISO string |
| `number` / `checkbox` | scalar |
| `relation` | list of page-ids |
| `formula` | the formula's resolved value |

So in `text_fields` write the property name as displayed in Notion's UI
(`Name`, `Description`), and that's what gets joined into the embedding.

## What each command does

| Command | Behaviour |
|---|---|
| `mfs ls /pages/` | lists pages shared with the integration. |
| `mfs ls /data_sources/<id>/` | `["records.jsonl", "schema.json"]`. |
| `mfs cat /pages/<id>.md` | walks the page's block children recursively, renders to markdown (headings, lists, code blocks, embeds). Cached as an artifact. |
| `mfs cat /data_sources/<id>/records.jsonl --range A:B` | paginated `data_sources.query` from cursor. |
| `mfs cat /data_sources/<id>/records.jsonl --locator '{"id":"..."}'` | `pages.retrieve(page_id)` + property flatten. |
| `mfs grep "PATTERN" /pages/<id>.md` | linear grep over the rendered markdown. |
| `mfs search "QUERY"` | Milvus only. Hits split between page chunks (`{path, lines}`) and db rows (`{locator: {id}}`). |

## Typical workflow

```bash
# 1. Create an integration, copy its secret.
export NOTION_TOKEN="ntn_xxx..."

# 2. In Notion: share each top-level page / database with the integration.

# 3. Register.
cat > notion-acme.toml <<'EOF'
credential_ref = "env:NOTION_TOKEN"
EOF
mfs add notion://acme --config notion-acme.toml

# 4. Search.
mfs search "kafka consumer lag runbook" --connector-uri notion://acme
# hit:  notion://acme/pages/abc123-...md  lines [120, 165]
mfs cat notion://acme/pages/abc123-....md --range 120:165

# db hit:  notion://acme/data_sources/def456-.../records.jsonl  locator: {"id":"ghi789-..."}
mfs cat notion://acme/data_sources/def456-.../records.jsonl --locator '{"id":"ghi789-..."}'

# 5. Refresh.
mfs add notion://acme --no-full
```

## Incremental sync

Pages: per-page fingerprint = `last_edited_time`.

Databases: per-database fingerprint = `count | max(last_edited_time across rows)`.
Per-row fingerprint = `last_edited_time` on the row. The connector queries
`filter: { last_edited_time: { after: <last_max> } }` to fetch only changed
rows.

Notion's API doesn't expose page deletions cleanly — deleted pages become
"archived" and the connector treats them as deleted on next sync.

## Gotchas

1. **Integration must be shared into pages**. The #1 confusion: token
   set, sync runs clean, **nothing appears**. Go to the page → Share menu
   → add the integration as a connection.
2. **Sub-pages inherit** the parent's integration share. Share the
   top-level workspace area once, all descendants are covered.
3. **Rich-text rendering** may lose formatting nuance (callouts, columns,
   synced blocks). `cat` shows you what the embedding sees.
4. **Database property names are case-sensitive** in `text_fields` /
   `metadata_fields` — they match exactly what Notion shows.
5. **Embeds / file blocks**: file/image blocks render as their URL or a
   placeholder; PDF/image content inside Notion is NOT fetched + indexed
   today.
6. **Workspace size**: Notion's `search` API returns up to 100 results
   per page; the connector paginates. Very large workspaces (10k+ pages)
   take a while on initial sync.
