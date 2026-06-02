# discord connector — ingest

URI: `discord://<alias>` (alias is your guild nickname).

## How to obtain credentials

You need a **Bot token** AND the **guild_id**.

**Bot token**:

1. Go to <https://discord.com/developers/applications> → **New
   Application** → name it.
2. Left sidebar → **Bot** → click **Add Bot** → **Reset Token** → copy.
3. **Privileged Gateway Intents** → enable **Message Content Intent**
   (required to read message text).
4. Left sidebar → **OAuth2** → **URL Generator** →
   - Scopes: `bot`
   - Bot Permissions: `View Channels`, `Read Message History`
   - Copy the generated URL, open it in a browser, pick the server
     (guild) to add the bot to. You must be guild owner or have Manage
     Server permission.

**Guild ID**:

In the Discord client, enable **Settings → Advanced → Developer Mode**.
Right-click the server name in the left sidebar → **Copy Server ID**.
That's a 17-19 digit numeric string.

## Required toml fields

| key | what |
|---|---|
| `token` | bot token (`env:DISCORD_BOT_TOKEN` recommended) |
| `guild_id` | numeric guild ID |

## Optional

| key | default | meaning |
|---|---|---|
| `max_read_rows` | 100000 | per-channel message cap |

No `[[objects]]` block — `discord.messages` preset auto-applies.

## URI tree

The connector exposes top-level text channels AND their active threads:

```
discord://<alias>/channels/general__<id>/messages.jsonl
discord://<alias>/channels/general__<id>/threads/<thread-name>__<id>/messages.jsonl
discord://<alias>/channels/announcements__<id>/messages.jsonl
```

Voice channels (type 2), category folders (type 4), and stage channels
(type 13) are intentionally skipped. Archived threads are NOT enumerated
(would require per-channel pagination).

## env: example

```toml
token = "env:DISCORD_BOT_TOKEN"
guild_id = "987654321098765432"
max_read_rows = 50000
```

```bash
export DISCORD_BOT_TOKEN=...
mfs add discord://acme-community --config /tmp/mfs-discord.toml
```

## Pitfalls

- **Message Content Intent disabled** → bot can read messages but
  `content` is empty. Toggle the intent in the Developer Portal and
  re-sync.
- **Bot not in the guild** → `403 Missing Access`. The OAuth URL step
  must complete successfully.
- **Private threads**: the bot only sees threads it was explicitly
  added to. Public threads under channels the bot can see are
  automatically accessible.
- **Discord rate limit**: ~50 req/sec per bot. Large guilds (hundreds
  of channels × deep history) take a while on first sync.
