# Discord (`discord`)

The `discord` connector indexes messages from the text channels of one Discord
server (guild), including its active threads.

## How MFS sees it

Top-level channels expose a message stream; active thread channels nest under
their parent:

```text
discord://community/
└── channels/
    ├── general__987654321/messages.jsonl
    └── eng__111222333/
        ├── messages.jsonl
        └── threads/
            └── incident-42__444555666/messages.jsonl
```

Unlike Slack, Discord has no per-message thread field — a thread is a separate
child channel. So the `discord.messages` preset indexes **one chunk per message**,
keyed by `id`, rendered with the author (`alice: deploy failed`). No `[[objects]]`
config is needed.

## Credentials

You need a **Bot token** and the **Guild ID** (the server's numeric ID).

**Bot token** — <https://discord.com/developers/applications> → *New
Application*:

1. *Bot* → *Add Bot* → *Reset Token* → copy it.
2. On the same page, enable **Message Content Intent** under *Privileged Gateway
   Intents*. Without it the bot connects but every message has empty `content`.
3. *OAuth2 → URL Generator* → scope `bot`, permissions *View Channels* +
   *Read Message History*. Open the generated URL and add the bot to your server
   (you need Manage Server permission).

![Discord applications page](https://github.com/user-attachments/assets/50d550fc-6e35-41e2-bbc2-deafa3aa81ca)

![Discord create app dialog](https://github.com/user-attachments/assets/59893fdb-b152-4f7f-83bd-8b4cf429080b)

![Discord bot token settings](https://github.com/user-attachments/assets/c6b2f7cd-0ef3-4de8-afdd-8389295f2be8)

![Discord message content intent](https://github.com/user-attachments/assets/19905907-5ac7-47ed-b285-59c824660834)

![Discord OAuth2 URL generator scopes](https://github.com/user-attachments/assets/70942093-8c13-4cd8-ab2a-39f65ea8777c)

![Discord bot permissions](https://github.com/user-attachments/assets/56c5ca0a-fd36-4454-94db-f7d20555a7ce)

**Guild ID** — enable *Settings → Advanced → Developer Mode*, then right-click the
server name → *Copy Server ID*.

## Configuration

```toml
token = "env:DISCORD_BOT_TOKEN"
guild_id = "987654321098765432"
max_read_rows = 50000
```

Save the file as `discord.toml`, then probe and index:

```bash
mfs connector probe discord://community --config ./discord.toml
mfs add discord://community --config ./discord.toml
```

## Sync and freshness

The connector tracks the latest `message_id` per channel as its cursor;
re-syncs fetch only newer messages. Deletion detection is `never`.

## Search and browse

```bash
mfs search "deploy failed" discord://community
mfs ls discord://community/channels/general__987654321
mfs cat discord://community/channels/general__987654321/messages.jsonl --locator '{"id":"1234567890123456789"}'
```

## Pitfalls

- Without **Message Content Intent**, message `content` comes back empty.
- Only text and announcement channels are enumerated.
- Only **active** threads are listed; archived threads aren't included.
- Each message is its own chunk — there's no thread-aggregate like Slack's.
