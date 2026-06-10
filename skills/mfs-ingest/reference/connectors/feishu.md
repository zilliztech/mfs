# feishu (lark) connector — ingest

URI: `feishu://<alias>`.

Indexes two subtrees in one connector: **group chats** (as message streams) and
**docx documents**.

## Credentials

Needs an **App ID** + **App Secret** from the Lark Developer Console:

1. <https://open.feishu.cn/app> (feishu / CN) or <https://open.larksuite.com/app> (lark / overseas).
2. **Create Custom App**, note the **App ID** (`cli_…`) and **App Secret**.
3. **Permissions & Scopes** → add (as **User Scopes**): `im:chat:readonly`,
   `im:message.group_msg:get_as_user`, `im:message.p2p_msg:get_as_user`,
   `drive:drive:readonly`, `docx:document:readonly`, `contact:user.id:readonly`.
   In larger orgs an admin must approve the scopes.

## Auth modes

### `user` (default, recommended)

Indexes everything the authorizing user can see. `mfs connector add feishu://<alias>`
runs a one-time browser authorization inline: **the user must open the printed URL and
approve — this consent can't be automated**, so surface the URL and wait for them.

The token then refreshes automatically **while the connector is actively synced**; if it
sits unused for several days the authorization expires and the next use reports it needs
re-auth. To re-authorize, run `mfs connector auth feishu://<alias>` and again have the
user approve the printed URL (existing index data is unaffected).

### `tenant` (app-only bot)

Set `auth = "tenant"`. The app sees only chats it has been added to and docs/folders
shared with it; add the bot to a chat by `@mentioning` it.

## toml fields

| key | default | meaning |
|---|---|---|
| `app_id` | — | `cli_…` app ID |
| `app_secret` | — | app secret (`env:FEISHU_APP_SECRET` recommended) |
| `auth` | `user` | `user` (OAuth) or `tenant` (app-only bot) |
| `region` | `feishu` | `feishu` (open.feishu.cn) or `lark` (open.larksuite.com) |
| `oauth_state_file` | `$MFS_HOME/feishu-<alias>.oauth.json` | OAuth token store (set by the wizard) |
| `docs_folder_token` | — | limit the docs subtree to one shared folder |
| `extra_chats` | — | include specific chats by id (`oc_…`), incl. p2p single chats |
| `extra_docs` | — | include specific docx by token |
| `max_read_rows` | 50000 | per-chat message cap |

## URI tree

```
feishu://<alias>/chats/<chat-name>__<chat-id>/messages.jsonl
feishu://<alias>/docs/<title>__<doc-token>.md
```

## Limiting scope (many docs)

User mode can enumerate your **entire** My Space. For a large account, index recent docs
first: estimate the size (optionally with a `since` date via `/v1/connectors/estimate`),
then `mfs add feishu://<alias> --since <date>` — only docs modified on/after `<date>` are
indexed; older ones are left alone (never deleted) and can be pulled in later by lowering
`--since`.

## Notes

- **p2p single chats** can't be auto-listed (Feishu API limit); include them with
  `extra_chats` — by `oc_…` chat id, or by the partner's `ou_…` open_id.
- **docs**: only docx is indexed. In user mode with no `docs_folder_token` / `extra_docs`,
  the connector enumerates your whole My Space; narrow it to one folder with
  `docs_folder_token`, or name specific docs with `extra_docs`. In tenant mode the app only
  sees docs/folders shared with it.
- **region**: `feishu` and `lark` are separate; an app from one can't auth the other.
