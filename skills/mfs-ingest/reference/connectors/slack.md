# slack connector — ingest

URI: `slack://<alias>` (alias is your workspace nickname).

## How to obtain credentials

You need a **Bot token** (recommended) or **User token**.

**Bot token (`xoxb-...`)** — for a Slack app the workspace admin installs:

1. Go to <https://api.slack.com/apps> and click **Create New App** →
   "From scratch".
2. Pick a name + workspace.
3. Left sidebar → **OAuth & Permissions** → scroll to "Bot Token Scopes"
   → add:
   - `channels:read` — list public channels
   - `channels:history` — read messages in public channels
   - `users:read` — list workspace users (for `/users.jsonl`)
   - `groups:read` + `groups:history` — for private channels (only if
     you want them)
   - `mpim:read` + `mpim:history` — for group DMs (rarely needed)
4. Scroll up → **Install to Workspace** → authorize.
5. Copy the **Bot User OAuth Token** (`xoxb-…`).
6. For private channels: invite the bot to each one (`/invite @your-bot`
   inside the channel). Public channels are visible without invite.

**User token (`xoxp-...`)** — for sources where you need the user's
own visibility (DMs the user is in, channels the user is in but the
bot isn't). Trade-off: token expires when the user revokes or rotates;
respects the user's per-message visibility.

## Required toml fields

| key | what |
|---|---|
| `token` | the `xoxb-…` or `xoxp-…` token (`env:SLACK_BOT_TOKEN` recommended) |

## Optional

| key | default | meaning |
|---|---|---|
| `channel_types` | `["public_channel"]` | which channel kinds; values: `public_channel`, `private_channel`, `mpim`, `im` |
| `oldest` | _none_ | history floor: unix ts OR `now-30d` syntax |
| `max_read_rows` | 100000 | per-channel message cap |

No `[[objects]]` block is needed — the `slack.messages` preset
auto-applies (`text_fields=["text"]`, `group_by="thread_ts"`,
`locator_fields=["thread_ts"]`). The `/users.jsonl` object likewise
gets `slack.users` preset (members searchable by name / real_name /
profile.title / profile.email).

## env: example

```toml
token = "env:SLACK_BOT_TOKEN"
channel_types = ["public_channel", "private_channel"]
oldest = "now-90d"
max_read_rows = 50000
```

```bash
export SLACK_BOT_TOKEN=xoxb-...
mfs add slack://acme --config /tmp/mfs-slack-acme.toml
```

## Pitfalls

- **Bot must be in private channels for `private_channel`**: even with
  the scope, the bot only sees private channels it was invited to.
- **`channels:history` rate limit**: tier-2 (~20 calls/min). Big
  workspaces (1000+ channels × full history) take significant wall
  time on first sync.
- **`oldest=now-30d`** is parsed by the connector; absolute Unix ts
  also works.
- **Bot's identity**: messages the bot itself sent appear in
  `messages.jsonl`. If that's noise, filter at search time with a
  metadata predicate on `user`.
- **Thread aggregation chunks**: a thread becomes ONE chunk (or a few
  sub-chunks if very long). Search results return the whole thread —
  expected, but users sometimes wonder why they get more text than
  they searched for.
