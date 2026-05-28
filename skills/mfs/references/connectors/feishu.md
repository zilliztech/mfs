# feishu connector (`feishu://` — Feishu / Lark)

## What this is

Feishu (a.k.a. Lark, ByteDance's enterprise messenger) — group chats AND docx
documents. Uses the official `lark-oapi` Python SDK (sync builder API,
wrapped in `asyncio.to_thread`). Two object kinds in one connector:

- **Group messages** — each chat's messages are a `message_stream`; engine
  groups by `thread_id` and emits thread-aggregate chunks (with size-bounded
  sub-chunking for long threads, see SKILL.md).
- **Docx documents** — each accessible doc is a `document` indexed by its
  body text. Discovery via a shared folder OR explicit per-doc tokens.

**When MFS helps**: Feishu-heavy org with project chats + a body of docx
knowledge-base material. Semantic search over both stays cross-referenced
under one connector handle.

**Known limit**: p2p single chats are NOT enumerable via REST (Feishu's
`chat.list` API explicitly excludes them). Webhook subscription would close
this gap; tracked as a future enhancement.

## URI shape

```
feishu://<alias>/                                            connector root
feishu://<alias>/chats/                                      group chats the app is a member of
feishu://<alias>/chats/<name>__<chat-id>/messages.jsonl      lazy message stream
feishu://<alias>/docs/                                       docx documents discovered for indexing
feishu://<alias>/docs/<title>__<doc-token>.md                rendered document body (text)
```

Each message has `{message_id, msg_type, create_time, sender, thread_id,
text}`. The `text` is extracted from `text` / `post` message bodies; rich
content like images, files, cards is summarised to their placeholder form
(e.g. `[image]`).

Each docx is fetched via `docx.v1.document.raw_content` (plain text body —
line breaks preserved, most formatting dropped; chunked via chonkie
RecursiveChunker for search).

## Auth — App credentials (app_id + app_secret)

```toml
app_id         = "cli_a0xxx..."
credential_ref = "env:FEISHU_APP_SECRET"      # the app secret
```

How to create. The Lark console (open.larksuite.com) is English; the Feishu
console (open.feishu.cn) is Chinese, so each menu label below has the Chinese
gloss in parentheses for users on the Feishu side.

1. open.larksuite.com or open.feishu.cn → Developer Console (开发者后台) →
   Apps (应用管理) → "Create App" (创建应用) → choose "Custom App"
   (企业自建应用).
2. Credentials & Basic Info (凭证与基础信息) → copy `App ID` + `App Secret`.
3. Permissions & Scopes (权限管理) → enable:
   - `im:message:read_only` — read messages in chats the app is in
   - `im:chat:read_only` — list / inspect chats
   - `contact:user.id:read_only` — resolve sender IDs (optional)
   - `drive:drive:readonly` — list files in shared folders (for docs)
   - `docx:document:readonly` — read docx body content
4. Version Management & Release (版本管理与发布) → Create Version (创建版本) →
   Submit for Review (申请上线) → wait for admin approval (管理员审核).
5. For chats: in each target group chat, invite the app as a member (the app
   must be a chat member to read its messages).
6. For docs: share either a FOLDER (preferred) or individual docx files with
   the app via the doc's "..." → "添加协作者" → select the app. See the
   discovery section below.

The SDK manages `tenant_access_token` exchange — we provide app_id /
app_secret, it handles the OAuth-like dance internally.

## Connector config TOML

```toml
# ─── auth (required) ───
app_id         = "cli_a0xxx..."
credential_ref = "env:FEISHU_APP_SECRET"

# ─── optional ───
# max_read_rows = 50000

# ─── docs discovery (pick one or both) ───
# Folder model (recommended): user creates a folder in Drive, shares it with the
# bot, drops docx files in. The bot recursively enumerates the folder on every sync.
# Find the folder_token in the Drive URL: https://xxx.feishu.cn/drive/folder/<TOKEN>
# docs_folder_token = "fldcnXXXXX"

# Explicit list: for docs outside the folder, share each with the bot individually
# ("..." -> "添加协作者") and list its token here. Find it in the doc URL:
# https://xxx.feishu.cn/docx/<TOKEN>
# extra_docs = [
#   { token = "ZsnVdP2IaoJei1xpIqScnZ64nqg", label = "Notes — Q2 planning" },
# ]

# Feishu has no built-in PRESET today — declare [[objects]] explicitly:
[[objects]]
match           = "/chats/*/messages.jsonl"
group_by        = "thread_id"
text_fields     = ["sender", "text"]
metadata_fields = ["msg_type", "create_time"]
locator_fields  = ["thread_id"]
```

## What each command does

| Command | Behaviour |
|---|---|
| `mfs ls /chats/` | `im.v1.chat.list` — chats the app is a member of. |
| `mfs ls /chats/<name>__<id>/` | `["messages.jsonl"]`. |
| `mfs cat .../messages.jsonl --range A:B` | `im.v1.message.list(container_id=chat_id, page_token=...)` paginated. |
| `mfs cat .../messages.jsonl --locator '{"thread_id":"..."}'` | fetches the messages with that thread/root id. |
| `mfs search "QUERY"` | Milvus only. Thread-granularity hits. |

## Typical workflow

```bash
# 1. Create the Feishu app, grant scopes, publish, get app_id + app_secret.
export FEISHU_APP_SECRET="..."

# 2. Invite the app as a member of each chat to be indexed.

# 3. Register.
cat > feishu-acme.toml <<'EOF'
app_id = "cli_a0xxx..."
credential_ref = "env:FEISHU_APP_SECRET"
[[objects]]
match = "/chats/*/messages.jsonl"
group_by = "thread_id"
text_fields = ["sender", "text"]
locator_fields = ["thread_id"]
EOF
mfs add feishu://acme --config feishu-acme.toml

# 4. Search.
mfs search "customer security compliance concerns" --connector-uri feishu://acme
mfs cat feishu://acme/chats/customer-acme__oc_xxx/messages.jsonl \
       --locator '{"thread_id":"..."}'

# 5. Refresh.
mfs add feishu://acme --no-full
```

## Incremental sync

Per-chat fingerprint = highest `create_time` seen. Refresh fetches messages
with `start_time > <last>` via paginated `message.list`.

## Gotchas

1. **App must be a member** of each chat — invite manually or programmatically.
   Without membership, `message.list` returns an authorization error.
2. **Scope review can be slow** in larger orgs — Admin approval is required
   for `im:message:read_only`. Plan for delay.
3. **Rich content lossy**: image / file / sticker / card messages render
   as their placeholder type. The `text` field gets the textual `text` /
   `post` body content; everything else is currently meta-only.
4. **`message_id` vs `thread_id`**: replies in the same thread share
   `thread_id` (or `root_id` on the root message). Locator keys on
   `thread_id` per the preset; sub-chunking carries `chunk_index` for
   long threads.
5. **Lark vs Feishu domains**: Lark (overseas, open.larksuite.com) and
   Feishu (China-only, open.feishu.cn) are separate cloud regions with separate
   app registries. Configure the SDK appropriately — the connector
   currently targets the default region via env (the SDK auto-detects from
   the app_id's domain).
6. **Rate limits**: Feishu's `message.list` is ~50 req/min per app per
   tenant. Initial syncs of large chat histories are slow.
