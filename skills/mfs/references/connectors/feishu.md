# feishu connector (`feishu://` — Feishu / Lark)

## Contents

This file is ~330 lines because Feishu / Lark has two auth modes, two
content subtrees (docs + chats), and a region split. Jump to the section
that matches the task:

- **Hard limits to know up-front** — read FIRST: API constraints (no
  full-text search, p2p chats not in `chat.list`, etc.) that shape what's
  achievable.
- **URI shape** — virtual filesystem layout.
- **Setup — `user` mode (OAuth Device Flow)** — personal identity; sees
  everything the user sees. Read when registering for personal use.
- **Setup — `tenant` mode (bot identity)** — bot identity; sees only docs
  / chats the bot is invited to. Read when registering an integration bot.
- **Connector config TOML — full reference** — region / auth / docs /
  chats / tuning. Open when writing the TOML.
- **What each command does** — per-command behaviour table. Open when a
  specific command (`grep`/`cat`/`search`/...) is misbehaving on this
  connector.
- **Typical workflow** — end-to-end recipe for both modes.
- **Where to find the IDs / tokens** — folder_token, doc_token, chat_id
  locations in URLs / UI. Open when you have a URL and need the token.
- **Incremental sync** — what gets re-fetched on re-run.
- **Gotchas** — recurring traps re-stated.

## What this is

Feishu (a.k.a. Lark) connector with **two subtrees** and **two auth modes**.
Subtrees:

- **`/chats/<name>__<chat-id>/messages.jsonl`** — message stream per chat,
  grouped into thread-aggregate chunks.
- **`/docs/<title>__<doc-token>.md`** — docx document body, chunked as text.

Auth modes — pick one explicitly in config (no "right default" — this is a
deliberate choice with real trade-offs):

- **`user`** — your own Feishu identity via OAuth Device Flow. Covers
  everything you see in Feishu: your groups, the docs you can read, and
  (via `extra_chats`) your p2p single chats. Tied to one human; the
  `refresh_token` auto-rotates on every sync and never expires **as long as
  you sync at least once per ~7 days**. Idle longer than that and you re-auth
  in the browser.

- **`tenant`** — a bot identity built on `app_id` + `app_secret`. The bot
  credential never expires, but the bot has to be **explicitly invited** to
  each group chat and **explicitly shared** into each doc/folder. It can
  see its OWN p2p with users (via `extra_chats`), not p2p chats between
  other people.

Which to pick:

| You want… | Mode |
|---|---|
| Index "what I personally see in Feishu" — groups, docs, my DMs | **user** |
| Personal MFS that you actually use weekly | **user** |
| A long-running shared service that may go idle for weeks at a time | **tenant** |
| A bot identity decoupled from any specific person (survives staff churn) | **tenant** |
| You want to avoid per-chat / per-doc share clicks | **user** |
| You want indexing to keep working even after the original auth-er leaves | **tenant** |

If you omit `auth` entirely, the connector defaults to `tenant` for
backward compatibility with older configs. New configs should set the
field explicitly.

## When MFS helps here

- Feishu workspaces with operational chat history (incidents, support, sales),
  internal RFCs in docx, plus the long-tail of 1-on-1 DMs that hold most of
  the actual decisions.
- Semantic search across all three at once.

## Hard limits to know up-front (Feishu's API design, not bugs)

1. **`chat.list` never returns p2p single chats.** Even with user OAuth and
   `im:chat:readonly`, p2p chats are excluded. They have to be supplied via
   `extra_chats` (literal chat_id or partner open_id).
2. **`drive.v1.file.list` does NOT list "shared with me" files.** Only the
   caller's own root or a folder the caller has explicit access to. So docs
   discovery requires the user to share a *folder* with the connector
   identity, OR list individual doc tokens.
3. **Bot scopes vs user-OAuth scopes are different namespaces.** Message
   reading for the bot uses `:readonly` (`im:message.group_msg:readonly`);
   the user-OAuth equivalent is `:get_as_user`
   (`im:message.group_msg:get_as_user`). The wrong one gets silently dropped
   from the OAuth grant.
4. **`refresh_token` is single-use** — every refresh issues a new one AND
   revokes the old one immediately. The connector handles this atomically
   (read + refresh + write-back inside `connect()`), but only if you point
   it at `oauth_state_file` (NOT `credential_ref` — credential_ref is
   read-only on purpose).

## URI shape

```
feishu://<alias>/                                       connector root
feishu://<alias>/chats/                                 enumerable chats
feishu://<alias>/chats/<name>__<chat-id>/messages.jsonl message stream (lazy)
feishu://<alias>/docs/                                  enumerable docx files
feishu://<alias>/docs/<title>__<doc-token>.md           docx body (eager but cached)
```

Each message has `{message_id, msg_type, create_time, sender, thread_id, text}`.
The `text` is extracted from `text` / `post` bodies (`[image]` placeholder for
non-text content).

## Setup — `user` mode (OAuth Device Flow)

1. open.larksuite.com or open.feishu.cn → Developer Console (开发者后台) →
   Apps (应用管理) → "Create App" (创建应用) → "Custom App" (企业自建应用).
2. Credentials & Basic Info (凭证与基础信息) → copy `App ID` + `App Secret`.
3. Permissions & Scopes (权限管理) → enable the **user-OAuth** scopes
   (note the `:get_as_user` suffix on the message scopes — Feishu has a
   separate bot namespace using `:readonly`, and silently drops it from a
   user-OAuth grant if you mix them up):
   - `im:chat:readonly`
   - `im:message.group_msg:get_as_user`
   - `im:message.p2p_msg:get_as_user`
   - `drive:drive:readonly`
   - `docx:document:readonly`
   - `contact:user.id:readonly`
4. Version Management & Release (版本管理与发布) → Create Version (创建版本)
   → submit for admin approval. Scopes don't take effect until a version is
   published.
5. On the MFS server host, run the Device Flow login once (add `--region lark`
   for the overseas tenant):

   ```bash
   python -m mfs_server.connectors.feishu.auth_login \
     --app-id cli_xxx \
     --app-secret-env FEISHU_APP_SECRET \
     --output ~/.feishu/oauth.json
     # --region lark      # uncomment for the overseas Lark tenant
   ```

   The script prints a URL + 8-char code. Open the URL in any browser, log
   in to Feishu as the user you want the connector to act as, approve.
   The script polls in the background and writes `oauth.json` when done.

6. Connector config:

   ```toml
   auth = "user"
   oauth_state_file = "/home/<you>/.feishu/oauth.json"
   ```

   No `app_id` / `credential_ref` here — they're inside the json.

**Token lifecycle (important if you go idle)**: access_tokens are ~2 h;
refresh_tokens are ~7 days from issue — but the plugin refreshes + rotates +
writes back the new refresh_token on **every** `connect()`. So as long as the
connector is exercised at least once per ~7 days (any `mfs add --no-full`,
`mfs cat` on a lazy object, etc.), it stays alive indefinitely. Idle longer
than that and you have to re-run `auth_login` in the browser.

## Setup — `tenant` mode (bot identity)

Pick this when you want a long-running indexer decoupled from any one
human's account, accepting that you'll manually invite the bot to each
chat / share into each doc.

1. open.larksuite.com or open.feishu.cn → Developer Console (开发者后台) →
   Apps (应用管理) → "Create App" (创建应用) → "Custom App" (企业自建应用).
2. Credentials & Basic Info (凭证与基础信息) → copy `App ID` + `App Secret`.
3. Permissions & Scopes (权限管理) → enable the **tenant** (bot) scopes:
   - `im:chat:read_only` — list chats the bot is in
   - `im:message:read_only` — read messages
   - `im:message.p2p_msg:readonly` — read p2p messages (bot variant)
   - `im:message.group_msg` — read group messages (bot variant)
   - `drive:drive:readonly` — list files in shared folders
   - `docx:document:readonly` — read docx bodies
4. Version Management & Release (版本管理与发布) → Create Version (创建版本)
   → submit for admin approval. Scopes don't take effect until a version is
   published.
5. Bot must be **explicitly added** to each chat (group: invite the bot
   manually; docs/folder: "..." → "添加协作者" → select the app).

Connector config:

```toml
auth = "tenant"
app_id = "cli_xxx..."
credential_ref = "env:FEISHU_APP_SECRET"
```

## Connector config TOML — full reference

```toml
# ─── region: pick ONE (default "feishu") ───
# region = "feishu"     # China — hosts open.feishu.cn / accounts.feishu.cn (default)
# region = "lark"       # overseas — hosts open.larksuite.com / accounts.larksuite.com

# ─── auth: pick ONE (no default; this is a deliberate choice) ───
auth = "user"                                         # human identity via OAuth
oauth_state_file = "/var/run/secrets/feishu/oauth.json"
# (region is recorded inside oauth.json by `auth_login --region`, so the
#  `region =` field above is optional in user mode.)

# OR

# auth = "tenant"                                     # bot identity
# app_id = "cli_xxx..."
# credential_ref = "env:FEISHU_APP_SECRET"

# ─── docs discovery (pick any combination, both optional) ───
# Folder model: user shares a Drive folder with the connector identity,
# connector recursively enumerates docx inside (incl. subfolders).
# Find folder_token in the URL: https://xxx.feishu.cn/drive/folder/<TOKEN>
docs_folder_token = "fldcnXXXXX"

# Explicit list: docs outside the folder. Find token in doc URL:
# https://xxx.feishu.cn/docx/<TOKEN>
extra_docs = [
  { token = "ZsnVdP2IaoJei1xpIqScnZ64nqg", label = "Q2 OKR notes" },
]

# ─── chats discovery (pick any combination) ───
# Note: groups the connector identity is a member of come from chat.list
# automatically. extra_chats covers p2p (which chat.list never returns)
# AND any group you want a friendlier label for.
extra_chats = [
  # Literal chat_id — fast, exact, no extra API call. Find it via
  # POST /open-apis/im/v1/chat_p2p/batch_query or via webhook events.
  { chat_id = "oc_efc1ce35c096f645d7b0dc2d879b7e65", label = "DM with Bob" },

  # Partner open_id auto-resolve — connector calls chat_p2p/batch_query on
  # connect() to find your p2p with this user. Friendlier: you only need
  # the partner's ou_xxx (visible on their Feishu profile). Bot's open_id
  # is in the developer console.
  { partner_open_id = "ou_60bcd808fec6049eaece331df5fe3d72", label = "DM with bot" },
]

# ─── tuning (optional) ───
# max_read_rows = 50000                              # per-chat message cap
```

PRESETS apply automatically — no `[[objects]]` needed for the standard
shape:

- `/chats/*/messages.jsonl` → `feishu.messages` preset
  (`text_fields=["text"]`, `group_by="thread_id"`, `locator_fields=["message_id"]`)
- `/docs/*.md` → indexed as `document` via chonkie RecursiveChunker

You can override these by writing `[[objects]]` with a `match` pattern, same
as any other connector.

## What each command does

| Command | Behaviour |
|---|---|
| `mfs ls /chats/` | `im.v1.chat.list` (groups only, by Feishu policy) PLUS any `extra_chats` resolved to chat_ids. |
| `mfs ls /chats/<name>__<id>/` | `["messages.jsonl"]`. |
| `mfs cat .../messages.jsonl --range A:B` | paginated `im.v1.message.list(container_id=chat_id)`. |
| `mfs cat .../messages.jsonl --locator '{"message_id":"om_xxx"}'` | exact message lookup. |
| `mfs cat /docs/<title>__<token>.md` | `docx.v1.document.raw_content` — flattened body text, cached as a `converted_md` artifact for re-cat speed. |
| `mfs cat /docs/<title>__<token>.md --range A:B` | line range slice of the cached body. |
| `mfs search "QUERY"` | Milvus only — hits are thread-aggregate chunks (each carrying `{thread_id, msg_range, chunk_index}` locator) for chats, and `{path, lines}` for docs. |
| `mfs grep PATTERN <path>` | linear scan of fetched content (no pushdown — Feishu's search API is a different surface). |

## Typical workflow

```bash
# ── tenant mode ──
export FEISHU_APP_SECRET="..."

# Share docs/folders with the bot in Feishu UI ("..." → 添加协作者),
# invite the bot into target groups.

cat > feishu-prod.toml <<'EOF'
auth = "tenant"
app_id = "cli_xxx..."
credential_ref = "env:FEISHU_APP_SECRET"
docs_folder_token = "fldcnXXXXX"
EOF
mfs add feishu://prod --config feishu-prod.toml

# ── user mode ──
python -m mfs_server.connectors.feishu.auth_login \
  --app-id cli_xxx --app-secret-env FEISHU_APP_SECRET \
  --output ~/.feishu/oauth.json
# Open the URL it prints, approve in browser.

cat > feishu-mine.toml <<'EOF'
auth = "user"
oauth_state_file = "/home/me/.feishu/oauth.json"
extra_chats = [
  { partner_open_id = "ou_60bcd808fec6049eaece331df5fe3d72", label = "DM with bot" },
  { partner_open_id = "ou_xxx_colleague_alice", label = "DM with Alice" },
]
EOF
mfs add feishu://mine --config feishu-mine.toml

# Search across both
mfs search "graceful shutdown signal handling" --connector-uri feishu://mine
mfs cat feishu://mine/chats/DM-with-bot__oc_efc1.../messages.jsonl \
       --locator '{"thread_id":"...", "chunk_index":3}'

# Re-sync — refresh_token auto-rotates; oauth.json gets updated in place
mfs add feishu://mine --no-full
```

## Where to find the IDs / tokens

| Need | Where to look |
|---|---|
| `app_id` / `app_secret` | Developer Console (开发者后台) → your app → 凭证与基础信息 |
| Folder token (`fldcnXXX`) | The folder's URL: `https://xxx.feishu.cn/drive/folder/<TOKEN>` |
| Doc token (e.g. `ZsnVdP2I...`) | The doc's URL: `https://xxx.feishu.cn/docx/<TOKEN>` |
| Chat ID (`oc_xxx`) | NOT in the Feishu web URL (SPA hides it). Easiest: have an admin user dump it via `chat_p2p/batch_query`, OR use `partner_open_id` instead and let the connector resolve it. |
| User's open_id (`ou_xxx`) — yours | `GET /open-apis/authen/v1/user_info` with your user_access_token returns yours |
| Another user's open_id | Visible on their Feishu profile card; also `GET /open-apis/contact/v3/users/<user_id>` |
| Bot's open_id | Developer Console (开发者后台) → your app → 应用功能 → 机器人 → bot's open_id is listed |

## Incremental sync

- **Chats**: every sync re-fetches the message list per chat (no fingerprint
  at the connector level; the engine's de-dup by chunk_id catches unchanged
  messages but they still go through the embed pipeline). For very active
  chats consider lowering `max_read_rows` to keep cost predictable.
- **Docs (folder)**: per-doc fingerprint = `modified_time` from Drive listing
  → unchanged docs skipped, no extra read.
- **Docs (extra_docs)**: per-doc fingerprint = `rev:<revision_id>` from
  `docx.v1.document.get` → unchanged docs skipped.
- **extra_chats with partner_open_id**: re-resolved every connect (cheap —
  one batch call covers all partners).

## Gotchas — re-stated

1. **`chat.list` excludes p2p.** Use `extra_chats` for any p2p chat (the
   connector identity has to be a participant for `message.list` to work —
   in tenant mode that means the bot is one of the two parties; in user
   mode it means the human is).
2. **`drive.file.list` won't show shared-with-me files.** Use
   `docs_folder_token` (share a *folder*, drop docs in) OR `extra_docs`.
3. **Right scope namespace**: tenant uses `:readonly`, user-OAuth uses
   `:get_as_user`. Wrong one gets silently dropped from the grant — you'll
   see `chat.list` work but `message.list` return empty bodies.
4. **`oauth_state_file` is NOT `credential_ref`.** Feishu's refresh_token is
   single-use; the file gets rewritten on every `connect()`. credential_ref
   is read-only by design and wouldn't survive engine config redaction
   anyway (`_redact_config` blacklists fields named `*token*`).
5. **`refresh_token` 7-day TTL**: rerun `auth_login` weekly, or build a cron
   that does it before expiry.
6. **Rich content lossy**: images / files / cards in messages become
   `[image]` / `[file]` / `[card]` placeholders. Search hits won't surface
   those payloads.
7. **Lark vs Feishu**: pick the cloud region with the `region` config field
   — `"feishu"` (default, `open.feishu.cn`, 国内) vs `"lark"` (overseas,
   `open.larksuite.com`). All API paths are identical between regions; only
   hostnames differ. For user mode pass `--region lark` to `auth_login` so
   the OAuth dance hits the right host (the value is persisted into
   `oauth.json` and read back on every connect).
