# discord connector (`discord://`)

## What this is

Discord text channels for a single guild (server). Uses Discord's REST API
v10 directly via `httpx` (no gateway, no real-time events). Each text
channel's messages are a `message_stream` — engine groups by thread (or
falls back to per-message for unrooted channel messages) and emits
thread-aggregate chunks with the same long-thread sub-chunking as slack.

**When MFS helps**: a community / OSS project on Discord with rich
historical discussion in #help / #dev / #bugs. Semantic thread search
across years of archive.

## URI shape

```
discord://<alias>/                                          connector root
discord://<alias>/channels/                                 text channels in the configured guild
discord://<alias>/channels/<name>__<channel-id>/messages.jsonl
```

Voice / category / forum channels are NOT exposed (the connector filters to
text channel types 0 / 5).

## Auth — Bot token

```toml
guild_id       = "892345..."
credential_ref = "env:DISCORD_BOT_TOKEN"      # value: bot token (NOT including the "Bot " prefix)
```

Where to create:
1. discord.com/developers/applications → "New Application"
2. Bot tab → "Reset Token" → copy the token
3. Privileged Intents: enable **MESSAGE CONTENT INTENT** (required to see
   message bodies — without it the API returns empty `content` fields)
4. OAuth2 → URL Generator → scopes: `bot`; bot permissions: `Read Messages
   / View Channels` + `Read Message History`
5. Open the generated URL, invite the bot to your guild

The `guild_id` is the server ID. To find it: Discord client → User
Settings → Advanced → enable Developer Mode → right-click the server icon
→ "Copy Server ID".

## Connector config TOML

```toml
# ─── auth + scope (required) ───
guild_id       = "892345678901234567"
credential_ref = "env:DISCORD_BOT_TOKEN"

# ─── optional ───
# max_read_rows = 100000      # cap per channel

# PRESET 'discord.messages' applied automatically:
#   group_by = "thread_id"
#   text_fields = ["content"]
#   metadata_fields = ["channel_id", "author", "timestamp"]
#   locator_fields = ["thread_id"]
```

## What each command does

| Command | Behaviour |
|---|---|
| `mfs ls /channels/` | `GET /guilds/<guild_id>/channels` filtered to text types. |
| `mfs ls /channels/<name>__<id>/` | `["messages.jsonl"]`. |
| `mfs cat .../messages.jsonl --range A:B` | `GET /channels/<id>/messages?before=<id>` paginated. |
| `mfs cat .../messages.jsonl --locator '{"thread_id":"..."}'` | reconstructs the thread by fetching the parent + replies. |
| `mfs search "QUERY"` | Milvus only. Hits at thread granularity. |

## Typical workflow

```bash
# 1. Create app, enable MESSAGE CONTENT intent, copy token, invite bot.
export DISCORD_BOT_TOKEN="MTAxx..."     # raw token; don't prefix with "Bot "

# 2. Register.
cat > discord-acme.toml <<'EOF'
guild_id = "892345678901234567"
credential_ref = "env:DISCORD_BOT_TOKEN"
EOF
mfs add discord://acme --config discord-acme.toml

# 3. Search by thread.
mfs search "stripe webhook duplicate event" --connector-uri discord://acme
mfs cat discord://acme/channels/help__123456/messages.jsonl --locator '{"thread_id":"..."}'

# 4. Refresh.
mfs add discord://acme --no-full
```

## Incremental sync

Per-channel fingerprint = highest `message_id` seen. Discord's REST
paginates with `before=<id>` (descending). Refresh fetches messages with
`after=<last_seen>`.

## Gotchas

1. **MESSAGE CONTENT intent is required** to read message bodies. Without
   it, `content` fields come back empty and search is useless — but the
   connector won't error. Enable it in the Bot panel.
2. **Bot must be a guild member** with `View Channels` + `Read Message
   History` on the channels you want indexed. Channel-level permissions
   override guild defaults.
3. **`Bot ` prefix is automatic** — provide the raw token in
   `credential_ref`; the connector prepends `Bot ` in the header itself.
4. **No DMs** — Discord bots can't read user DMs.
5. **No forum / thread channel types** today (channel type 11/15). Future
   enhancement.
6. **Rate limit**: Discord caps message reads to ~50 req/sec per token.
   Large channels with 100k+ messages take a while on initial sync.
