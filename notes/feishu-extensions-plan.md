# Feishu connector — extensions plan (working notes, not a formal design doc)

Tracking three follow-ups to the Feishu connector. Lives here (not under `design/`)
because it is a working/transient plan tied to active execution; once landed, the
condensed learnings move into `design/04-connector-and-ingest.md` or a connector
ADR.

## Context

Today's Feishu connector uses `tenant_access_token` (bot identity) and exposes
group messages only. Three known limits:

1. **Documents** (docx / drive / wiki) — completely unsupported.
2. **User-personal scope** — bot can only see chats it has been explicitly added to.
3. **P2P single chats** — Feishu API `chat.list` is documented to NOT return p2p,
   even with user token. The cost-effective bypass for "I want chat X with Bob
   specifically" is a user-supplied chat_id list.

Webhook subscription would close every remaining gap but inverts the architecture
(MFS becomes long-running event-driven instead of REST poller). Deferred to v1.x.

## Priorities

### P0 — Feishu Docs connector (1-2 days; pure REST, tenant token reusable)

Add a `docs/` subtree alongside `chats/` in the same `feishu://` connector.

**URI shape additions:**
```
feishu://<alias>/docs/<doc_id>.md          rendered markdown of a docx document
feishu://<alias>/wiki/<space_id>/<node_id>.md   (later — wiki nodes; P0.5 maybe)
```

**Scopes user must enable on the app (open.feishu.cn → 权限管理):**
- `docx:document:readonly` — read docx documents
- `drive:drive:readonly` — list drive files (to discover doc_ids)
- (later) `wiki:wiki:readonly` — wiki space access

After adding scopes, **must publish new app version + admin approve**.

**Discovery model:**
- Use Drive API `files.list` with filter `type=docx` to enumerate accessible docs
  (= docs the bot is a collaborator on). User must share each target doc with
  the bot via the doc's "..." → "添加协作者".
- Or: enumerate the bot's "我的空间" content if the API supports it for bots.

**Block → markdown rendering:**
- `GET /open-apis/docx/v1/documents/{doc_id}/blocks` returns the block tree.
- Block types to handle: `page` (root), `text`, `heading1..9`, `bullet`,
  `ordered`, `code`, `quote`, `divider`, `image`, `table`, `link`. Same shape
  as Notion's block-to-md rendering.
- One full pass per sync; cache as `converted_md` artifact.

**Sync model:**
- Fingerprint per doc = `obj_edit_time` (or `revision_id` if exposed).
- New / changed / deleted docs detected via Drive listing diff.

**User actions required BEFORE I can run the e2e:**
1. Enable scopes `docx:document:readonly` + `drive:drive:readonly` on the app.
2. Publish new app version.
3. Share at least 1 doc with the bot (any test doc with body content).

**Status:** code not started yet.

---

### P1 — OAuth Device Flow user-token mode (2-3 days)

Add `auth = "user"` option to feishu (and later slack) config. When set, the
connector authenticates as a real human via OAuth 2.0 **Device Flow** (NOT
Authorization Code flow — no redirect URI needed, MFS server stays headless).

**Flow:**
```
POST https://open.feishu.cn/open-apis/authen/v2/oauth/device_authorization
  → { device_code, user_code, verification_url, expires_in, interval }

MFS prints "Open <verification_url>, enter code <user_code>"

User opens in browser, logs in, approves scopes, browser shows "success".

MFS polls POST https://open.feishu.cn/open-apis/authen/v2/oauth/token
  every <interval> seconds until response has access_token + refresh_token.

Store refresh_token to a file the user can credential_ref later:
  ~/.mfs/feishu-<alias>-refresh-token.txt
```

**Token refresh:**
- access_token lifetime 2h; refresh_token lifetime 30 days.
- On each `connect()`, exchange refresh_token for fresh access_token.
- If refresh_token expired (> 30 days), surface a clear error: "OAuth expired,
  re-run `mfs feishu auth login feishu://<alias>`".

**Connector config addition:**
```toml
auth = "user"                                                    # default "tenant"
credential_ref = "file:/root/.mfs/feishu-prod-refresh-token.txt" # the refresh_token
# app_id + app_secret still needed (for token exchange auth):
app_id = "cli_a9636a..."
app_secret_ref = "env:FEISHU_APP_SECRET"
```

**Effect on existing endpoints:**
- `chat.list` returns groups the USER is in (not bot). Likely still excludes p2p.
- `message.list(container_id=group)` returns full history the user can see.

**CLI subcommand (need to add):**
- `mfs feishu auth login <connector-uri>` — runs the device flow interactively,
  saves refresh token at a known path, prints the path for `credential_ref`.

**User actions required:**
1. App must have user-token scopes added (in addition to tenant): same
   `im:message.group_msg:readonly`, plus optionally `im:chat:readonly`,
   `docx:document:readonly`, etc.
2. User runs `mfs feishu auth login feishu://prod`, completes browser approval.
3. Then `mfs add feishu://prod --config <toml>` with `auth = "user"`.

**Status:** code not started yet.

---

### P1.5 — Explicit `extra_chats` config (half day; closes the p2p gap)

User provides chat_ids of p2p chats they want indexed (since auto-discovery is
impossible without webhook). The connector enumerates each as
`/extra/<sanitized-label>__<chat_id>/messages.jsonl`.

**Config addition:**
```toml
extra_chats = [
  { chat_id = "oc_xxx", label = "DM with Bob" },
  { chat_id = "oc_yyy", label = "DM with Alice" },
]
```

**How user gets chat_ids:** Feishu web → open the conversation → URL bar shows
`https://feishu.cn/messenger/oc_xxx` (or similar). Copy the `oc_xxx` portion.

**Sync model:**
- For each `extra_chats[i]`, emit ObjectChange with the constructed path.
- `message.list(container_id=chat_id, container_id_type='chat')` for history.
- Fingerprint same shape as the existing chats path.

**Auth requirement:** works under both tenant AND user token, but for a
human-to-human p2p, only **user token** can see the messages. Under tenant
(bot) token, attempting to read another user's p2p will 403.

**User actions required:**
- After OAuth + scopes done (P1), user finds the chat_ids in Feishu web and
  drops them in config.

**Status:** code not started yet.

---

## Execution checklist

- [x] **P0.1** — Implement `/docs/<title>__<token>.md` subtree (docx body via
      `docx.v1.document.raw_content`, recursive folder enumeration via
      `drive.v1.file.list(folder_token)`, plus `extra_docs` escape hatch).
- [x] **P0.2** — Update docs (`feishu.md`).
- [x] **P0.3** — User granted `drive:drive:readonly` + `docx:document:readonly`
      and shared one docx with the bot.
- [x] **P0.4** — Live e2e (`phase13_feishu_docs_smoke`) 6/6 green against the
      user's shared doc (8.8KB Chinese markdown → 5 chunks, search hits all 5).

**P0 design pivot discovered during execution**: `drive.v1.file.list` without a
folder_token returns the BOT's own root, which is empty on a fresh enterprise
app. "Share with bot" grants READ but does NOT make the file appear in
file.list. So discovery cannot rely on auto-listing; it needs the folder-share
model. This is now reflected in the plugin (`docs_folder_token` for the bulk
path, `extra_docs` for the escape hatch) and the connector reference doc.

- [x] **P1.1** — Device Flow handshake + refresh helpers (`feishu/oauth.py`) + CLI
      entry point (`feishu/auth_login.py`). Endpoints copied from larksuite/cli:
      device_authorization at `https://accounts.feishu.cn/oauth/v1/device_authorization`,
      token at `https://open.feishu.cn/open-apis/authen/v2/oauth/token`.
- [x] **P1.2** — `auth = "user"` switch in connect() — reads `oauth_state_file`,
      refreshes, threads `RequestOption.user_access_token(...)` into every SDK call.
- [x] **P1.3** — User did the Device Flow dance, granted 6 of 7 requested scopes
      (message scopes denied by tenant policy; the rest worked).
- [x] **P1.4** — Live e2e `phase13_feishu_user_smoke` 8/8 green: connect, rotation,
      write-back, app_id/secret preserved, extra_docs indexed via USER token, cat+search.

**Three bugs discovered + fixed during P1**:
1. **Refresh token is ONE-SHOT** — Feishu revokes the old refresh_token the moment
   it issues a new one. The first connect() crashed the second because the rotated
   token wasn't persisted. Fix: atomic write-back of the new token to oauth.json
   inside connect(), before any other API call could fail.
2. **Field name `oauth_token_file` got REDACTED** — engine's `_SECRET_SUBSTRINGS`
   blacklist contains "token", so any config key with "token" in the name was
   replaced with `<redacted>` before persistence. Fix: renamed to `oauth_state_file`.
3. **Granted scopes != requested scopes** — Feishu silently dropped
   `im:message.group_msg:readonly` and `im:message.p2p_msg:readonly` from the user's
   consent grant (admin policy?). User mode therefore can list chats + read drive +
   read docs, but CAN'T read message content. Logged but not blocking — user OAuth
   for message content can be reconsidered if admin loosens the scope policy.

**Refresh token TTL gotcha**: Feishu's `refresh_token_expires_in` came back as
604800 (7 days), not the 2592000 (30 days) often cited. So `auth_login` must be
re-run at most weekly. Code already uses the value Feishu returns; no hardcoded
30 days.
- [x] **P1.5.1** — Add `extra_chats` config field + sync wiring (in `_chats()`,
      merged with `chat.list` results, de-duped by chat_id).
- [x] **P1.5.2** — Asked user to find chat_id; F12 dev tools failed (Feishu web
      uses protobuf). Pivoted to `POST /open-apis/im/v1/chat_p2p/batch_query`
      (endpoint spec from larksuite-cli source: `chatter_id_type=open_id`,
      body `{"chatter_ids": [...]}`, response `data.p2p_chats[].chat_id`).
      Successfully resolved the user's p2p with bot:
      `oc_efc1ce35c096f645d7b0dc2d879b7e65`.
- [x] **P1.5.3** — Live e2e (`phase13_feishu_user_smoke` extended): 10/10 green.
      The p2p chat was indexed with 34 thread_aggregate chunks. Confirms
      `im:message.p2p_msg:get_as_user` scope grants real message-body access
      under user OAuth — the missing piece from the previous round.

**Bonus discovered during P1.5**: `im:message.group_msg:readonly` was the WRONG
scope name namespace; the user-OAuth equivalent is `im:message.group_msg:get_as_user`
(verified by reading larksuite-cli's `shortcuts/im/im_chat_messages_list.go` which
splits its declarations into `BotScopes` and `UserScopes`). Fixed in
`DEFAULT_SCOPES` and re-OAuth confirmed the message scopes are now granted.

Also added `feishu.messages` to `connectors/base.py:PRESETS` so users don't have
to write `[[objects]]` for `text_fields=["text"]` — same model as slack/discord/
gmail. The plugin's `preset_for()` returns the key.

**Future enhancement (not P1.5)**: wire `chat_p2p/batch_query` into the connector
itself — let users put a list of partner open_ids in config and have the connector
resolve those into chat_ids automatically. Right now users have to provide the
already-resolved chat_id, which requires running a one-off script. Track as a
v0.5 polish item.
- [ ] **Cleanup** — fold the condensed result into `design/04-connector-and-ingest.md`
      or a connector ADR; delete this working notes file.

## Out of scope (explicit, do NOT do this round)

- Webhook subscription / event-driven mode for p2p auto-discovery.
- Discord user-token / self-bot (TOS-violating, irrelevant).
- Wiki spaces (P0.5 — postpone unless user asks).
- Sheets / Bitable / Calendar / Mail data sources.
