# Slack (`slack`)

The `slack` connector indexes channel messages and the workspace user directory.
Messages are grouped into threads so a search hit reopens the whole conversation,
not a single isolated line.

## How MFS sees it

Each channel exposes a message stream; the workspace has one user directory:

```text
slack://acme/
├── channels/
│   ├── eng-backend__C012345/messages.jsonl   message_stream
│   └── general__C067890/messages.jsonl
└── users.jsonl                                record_collection
```

Built-in presets mean no `[[objects]]` config is needed:

- `slack.messages` groups messages by `thread_ts` and embeds each thread as one
  chunk, rendered with speaker identity (`U012345: deploy failed`) so search has a
  stronger signal.
- `slack.users` indexes each member's name, real name, display name, title, and
  email — so `mfs search "VP of Engineering" slack://acme/users.jsonl` works.

Message `user` values are Slack IDs (`U…`); resolve them to names via
`users.jsonl`.

## Credentials

You need a **Bot token** (`xoxb-…`, recommended) or a **User token** (`xoxp-…`,
when the bot can't see what you can).

**Bot token** — <https://api.slack.com/apps> → *Create New App* → *From scratch*:

1. *OAuth & Permissions* → add Bot Token Scopes:
    - `channels:read`, `channels:history` — list and read public channels
    - `users:read` — the user directory (`users.jsonl`)
    - `groups:read` + `groups:history` — private channels (optional)
    - `mpim:read` + `mpim:history` — group DMs (optional)
2. *Install to Workspace* → authorize → copy the **Bot User OAuth Token**
   (`xoxb-…`).
3. For each **private** channel, invite the bot from inside it: `/invite @bot`.
   Public channels need no invite once scopes are granted.

A **User token** (`xoxp-…`) is created the same way under *User Token Scopes*; use
it when the bot identity can't reach DMs or un-invited channels. It rotates when
the user revokes access.

## Configuration

```toml
token = "env:SLACK_BOT_TOKEN"
channel_types = ["public_channel"]   # + private_channel, mpim, im
oldest = "now-30d"                    # optional history floor
max_read_rows = 50000
```

## Sync and freshness

The connector tracks each channel's latest message `ts` as its cursor, so
re-syncs only fetch newer messages. Slack messages are append-mostly, so deletion
detection is `never` — edited or deleted messages aren't retroactively pruned.

## Search and browse

```bash
mfs add slack://acme --config ./slack.toml

mfs search "deploy failed" slack://acme/channels/eng-backend__C012345/messages.jsonl
mfs search "Alice Wang" slack://acme/users.jsonl
mfs cat slack://acme/channels/eng-backend__C012345/messages.jsonl --locator '{"thread_ts":"1717123456.001200"}'
```

## Pitfalls

- Private channels need both the scopes **and** bot membership.
- Hits are thread aggregates, so a short query can reopen a long thread.
- `max_read_rows` applies per channel and can mark recall partial.
