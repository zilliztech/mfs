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

- [ ] **P1.1** — Implement Device Flow handshake + token refresh + CLI subcommand.
- [ ] **P1.2** — Add `auth = "user"` switch in connector connect().
- [ ] **P1.3** — `[STOP]` Ask user to grant user-token scopes + run auth login.
- [ ] **P1.4** — Live e2e for OAuth user mode. Commit.
- [ ] **P1.5.1** — Add `extra_chats` config field + sync wiring.
- [ ] **P1.5.2** — `[STOP]` Ask user for chat_ids to test against.
- [ ] **P1.5.3** — Live e2e for extra_chats. Commit.
- [ ] **Cleanup** — fold the condensed result into `design/04-connector-and-ingest.md`
      or a connector ADR; delete this working notes file.

## Out of scope (explicit, do NOT do this round)

- Webhook subscription / event-driven mode for p2p auto-discovery.
- Discord user-token / self-bot (TOS-violating, irrelevant).
- Wiki spaces (P0.5 — postpone unless user asks).
- Sheets / Bitable / Calendar / Mail data sources.
