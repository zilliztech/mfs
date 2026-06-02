# feishu (lark) connector — ingest

URI: `feishu://<alias>`.

## How to obtain credentials

Feishu / Lark requires an **App ID** and **App Secret** from the Lark
Developer Console, plus one of two auth modes.

**Create the app**:

1. Go to <https://open.feishu.cn/app> (CN) or <https://open.larksuite.com/app> (US).
2. **Create Custom App** → name + icon.
3. Note the **App ID** (`cli_...`) and **App Secret**.

**Two auth modes — pick one**:

### `auth = "internal"` (tenant / bot)

App acts as itself. Easier to set up, but limited visibility — only
chats / docs the app has been explicitly added to or shared with.

- In the developer console → **Permissions & Scopes** → add:
  - `im:message:readonly` — read messages
  - `im:chat:readonly` — list chats
  - `docx:document:readonly` — read docx documents
  - `drive:drive:readonly` — list drive items
- **Version Management & Release** → request approval from your tenant
  admin.
- Add the bot to chats by mentioning it (`@bot-name`) or pinning it via
  group admin settings.

### `auth = "oauth"` (user identity, recommended)

App acts on behalf of a real user. Sees everything that user sees.
Requires user-OAuth Device Flow login.

- Same scopes as above but as **User Scopes** (not Bot Scopes).
- Run the auth flow once:
  ```bash
  uv run python -m mfs_server.connectors.feishu.auth_login --app-id <id> --app-secret <secret> --region cn
  ```
  This opens a browser, user authorizes, and the resulting
  `oauth.json` lands at `$MFS_HOME/feishu.oauth.json` by default.
- The plugin refreshes the token on every connect and atomically
  rotates the refresh_token (Feishu refresh tokens are one-shot, so the
  plugin must own R/W of the file — that's why `oauth_state_file` is a
  path, not a `credential_ref`).

## Required toml fields

| key | what |
|---|---|
| `app_id` | `cli_…` app ID from the developer console |
| `app_secret` | app secret (`env:FEISHU_APP_SECRET` recommended) |

## Optional

| key | default | meaning |
|---|---|---|
| `region` | `cn` | `cn` (api.feishu.cn) or `us` (api.larksuite.com) |
| `auth` | `oauth` | `oauth` (user) or `internal` (tenant/bot) |
| `oauth_state_file` | `$MFS_HOME/feishu.oauth.json` | path to the OAuth state JSON |
| `docs_folder_token` | _none_ | limits the docs subtree to one folder |
| `extra_chats` | _none_ | extra chat IDs to include (`oc_xxx`) — for tenant mode where chat.list may miss some chats |
| `max_read_rows` | 100000 | per-chat message cap |

## URI tree

```
feishu://<alias>/chats/<chat-name>__<chat-id>/messages.jsonl
feishu://<alias>/docs/<title>__<doc-token>.md
```

Two subtrees in one connector — chats AND docs. The docs subtree only
appears when the app has docx read scope and is shared with the
documents (or the user has access in OAuth mode).

## env: example (OAuth mode)

```toml
app_id = "cli_a1b2c3d4e5f6"
app_secret = "env:FEISHU_APP_SECRET"
region = "cn"
auth = "oauth"
oauth_state_file = "/home/zhangchen/.mfs/feishu.oauth.json"
docs_folder_token = "fldcnXXXXXX"   # only docs under this folder
```

```bash
export FEISHU_APP_SECRET=...
# one-time OAuth setup:
uv run python -m mfs_server.connectors.feishu.auth_login --app-id cli_... --app-secret $FEISHU_APP_SECRET --region cn
# then:
mfs add feishu://acme --config /tmp/mfs-feishu.toml
```

## Pitfalls

- **Tenant mode misses p2p single chats**: `im.v1.chat.list` doesn't
  enumerate them. Use `extra_chats` to include them by ID.
- **OAuth refresh_token rotation**: if the file gets corrupted or
  copied to another host without atomic write, the rotated token
  becomes orphaned and login breaks. Always let the plugin own the
  file.
- **Region mismatch**: `cn` and `us` are separate endpoints with
  separate app registries. App from one region can't auth against the
  other.
- **Scope approval**: in larger orgs, an admin must approve the
  app's scopes before they become effective.
