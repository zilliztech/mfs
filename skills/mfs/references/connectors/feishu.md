# feishu connector (`feishu://` — Feishu / Lark)

## What this is

Feishu (a.k.a. Lark) connector with **two subtrees** and **two auth modes**.
Subtrees:

- **`/chats/<name>__<chat-id>/messages.jsonl`** — message stream per chat,
  grouped into thread-aggregate chunks.
- **`/docs/<title>__<doc-token>.md`** — docx document body, chunked as text.

Auth modes:

- **`tenant` (default)** — bot identity (`app_id` + `app_secret`). Covers chats
  the bot was invited to + docs the bot was shared into.
- **`user`** — your own identity via OAuth Device Flow. Covers your groups,
  your docs, and (via `extra_chats`) your p2p single chats.

Pick `tenant` for headless / shared / never-changing setups; pick `user` when
you want to index "what I personally see".

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

## Choosing an auth mode

| You want… | Use |
|---|---|
| A bot you control to index public-ish content (announcements / docs / shared groups) | **tenant** |
| Index your personal history (your DMs, your private docs, groups you're in but bot isn't) | **user** |
| Server-runs-forever, no per-user account, no token refresh dance | **tenant** |
| Index everything one specific person sees | **user**, one connector per person |

## Setup — `tenant` mode

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
auth = "tenant"                       # this is the default; can be omitted
app_id = "cli_xxx..."
credential_ref = "env:FEISHU_APP_SECRET"
```

## Setup — `user` mode (OAuth Device Flow)

1. Repeat steps 1-4 above with the **user-OAuth** scope namespace instead
   (`:get_as_user` for message scopes, others unchanged):
   - `im:chat:readonly`
   - `im:message.group_msg:get_as_user`
   - `im:message.p2p_msg:get_as_user`
   - `drive:drive:readonly`
   - `docx:document:readonly`
   - `contact:user.id:readonly`
2. On the MFS server host, run the Device Flow login once:

   ```bash
   python -m mfs_server.connectors.feishu.auth_login \
     --app-id cli_xxx \
     --app-secret-env FEISHU_APP_SECRET \
     --output ~/.feishu/oauth.json
   ```

   The script prints a URL + 8-char code. Open the URL in any browser, log
   in to Feishu as the user you want the connector to act as, approve.
   The script polls in the background and writes `oauth.json` when done.

3. Connector config:

   ```toml
   auth = "user"
   oauth_state_file = "/home/<you>/.feishu/oauth.json"
   ```

   No `app_id` / `credential_ref` here — they're inside the json.

**Token lifecycle**: access_tokens last 2 h; refresh_tokens last ~7 days
(Feishu's actual TTL, not the 30 days some docs cite). The connector refreshes
on every `connect()` and writes the rotated refresh_token back atomically. If
you don't touch the connector for 7+ days, the refresh_token expires and you
have to re-run `auth_login`.

## Connector config TOML — full reference

```toml
# ─── auth: pick ONE ───
auth = "tenant"                                       # bot identity (default)
app_id = "cli_xxx..."
credential_ref = "env:FEISHU_APP_SECRET"

# OR

# auth = "user"                                       # human identity (OAuth)
# oauth_state_file = "/var/run/secrets/feishu/oauth.json"

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
7. **Lark vs Feishu**: this connector currently targets `open.feishu.cn`
   (China region). Lark (overseas, `open.larksuite.com`) uses the same API
   shapes but different hostnames — supporting it cleanly is a future
   parameterisation.
