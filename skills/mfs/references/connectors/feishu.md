# feishu connector (`feishu://` — Feishu / Lark)

## What this is

Feishu (a.k.a. Lark, ByteDance's enterprise messenger) group chats. Uses
the official `lark-oapi` Python SDK (sync builder API, wrapped in
`asyncio.to_thread`). Each group chat's messages are a `message_stream`;
engine groups by `thread_id` (with `root_id` / per-message fallback) and
emits thread-aggregate chunks.

**When MFS helps**: Feishu-heavy org with project chats, sales/support
chats, knowledge-channel discussions. Semantic search over the chat
archive is otherwise painful — Feishu's built-in search is keyword + recent.

## URI shape

```
feishu://<alias>/                                           connector root
feishu://<alias>/chats/                                     group chats the app is in
feishu://<alias>/chats/<name>__<chat-id>/messages.jsonl     lazy message stream
```

Each message has `{message_id, msg_type, create_time, sender, thread_id,
text}`. The `text` is extracted from `text` / `post` message bodies; rich
content like images, files, cards is currently summarised to their
placeholder form (e.g. `[image]`).

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
4. Version Management & Release (版本管理与发布) → Create Version (创建版本) →
   Submit for Review (申请上线) → wait for admin approval (管理员审核).
5. **In each target chat**, invite the app as a member (the app must be a
   chat member to read its messages).

The SDK manages `tenant_access_token` exchange — we provide app_id /
app_secret, it handles the OAuth-like dance internally.

## Connector config TOML

```toml
# ─── auth (required) ───
app_id         = "cli_a0xxx..."
credential_ref = "env:FEISHU_APP_SECRET"

# ─── optional ───
# max_read_rows = 50000

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
