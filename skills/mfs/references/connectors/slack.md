# slack connector (`slack://`)

## What this is

Slack workspace exposed as a tree of channels. Each channel's messages
are a `message_stream` — the engine groups them by `thread_ts` and emits
**thread-aggregate** chunks (one chunk per thread, sub-chunked into
1500-char windows for very long threads, with a 2-message overlap so
cross-message references survive embedding).

**When MFS helps**: a Slack workspace with years of #support, #incidents,
#ops history. Semantic search over thread-level context — "what did we do
last time the DB failover stalled" — instead of Slack's word-level search.

## URI shape

```
slack://<alias>/                                            connector root
slack://<alias>/channels/                                   all channels
slack://<alias>/channels/<name>__<channel-id>/              one channel (id suffix disambiguates renames)
slack://<alias>/channels/<name>__<channel-id>/messages.jsonl  lazy message stream
slack://<alias>/users.jsonl                                 workspace users
```

The `__<channel-id>` suffix is critical — channel names are mutable and
non-unique in Slack history.

## Auth — Bot token

```toml
credential_ref = "env:SLACK_BOT_TOKEN"     # value: "xoxb-..."
```

How to create:
1. api.slack.com/apps → "Create New App" → "From scratch"
2. "OAuth & Permissions" → Scopes → Bot Token Scopes — add:
   - `channels:history` (read public channel messages)
   - `channels:read` (list public channels)
   - `groups:history` + `groups:read` (private channels — only if needed)
   - `users:read` (resolve user IDs to names)
3. "Install to Workspace" — copy the **Bot User OAuth Token** (`xoxb-...`)
4. Invite the bot to each channel you want indexed (private channels need
   explicit invite; public channels are accessible after install if you
   include `channels:read` + `channels:history` scopes).

## Connector config TOML

```toml
# ─── auth (required) ───
credential_ref = "env:SLACK_BOT_TOKEN"

# ─── scope ───
# channel_types = ["public_channel"]     # default; add "private_channel" if you want those
# oldest = 1704067200                    # unix-ts lower bound — index only messages newer than this
# max_read_rows = 100000                 # cap per channel

# Built-in PRESET 'slack.messages' applies automatically:
#   group_by = "thread_ts"
#   text_fields = ["text"]
#   metadata_fields = ["channel", "user", "ts"]
#   locator_fields = ["thread_ts"]
# You can override any of these via [[objects]] if you have a reason.
```

The thread-aggregate path is engine-internal — there's no `chunk_strategy`
to set. Long threads automatically get split into multiple sub-chunks
(carried with `chunk_index` + `msg_range` in the locator).

## What each command does

| Command | Behaviour |
|---|---|
| `mfs ls /channels/` | `conversations.list` filtered by `channel_types`. |
| `mfs ls /channels/<name>__<id>/` | `["messages.jsonl"]`. |
| `mfs cat /channels/<name>__<id>/messages.jsonl --range A:B` | `conversations.history(channel, cursor)` paged. |
| `mfs cat .../messages.jsonl --locator '{"thread_ts":"1716..."}'` | `conversations.replies(channel, ts)` — full thread. |
| `mfs cat /users.jsonl` | `users.list` (paged). |
| `mfs search "QUERY"` | Milvus only. Hits return **threads**, not single messages, locator `{thread_ts}` (+ `chunk_index` / `msg_range` for very long threads). |
| `mfs grep "PATTERN"` | linear scan of fetched messages. |

## Typical workflow

```bash
# 1. Create the Slack app, install, invite the bot to channels.
export SLACK_BOT_TOKEN="xoxb-..."

# 2. Register.
cat > slack-acme.toml <<'EOF'
credential_ref = "env:SLACK_BOT_TOKEN"
channel_types = ["public_channel"]
oldest = 1704067200      # only index 2024-01-01 onward
EOF
mfs add slack://acme --config slack-acme.toml

# 3. Search by thread context.
mfs search "DB failover stalled" --connector-uri slack://acme
# hit:  slack://acme/channels/incidents__C0123456/messages.jsonl
#       locator: {"thread_ts":"1716230400.123456"}
mfs cat slack://acme/channels/incidents__C0123456/messages.jsonl \
       --locator '{"thread_ts":"1716230400.123456"}'

# 4. Refresh — only new messages since the last cursor.
mfs add slack://acme --no-full
```

## Incremental sync

Per-channel fingerprint = `latest_ts` cached in `connector_state`. Re-sync
fetches `conversations.history(channel, oldest=<last_ts>)` and appends.
Slack messages are append-only in practice; edits show up via a separate
event the connector currently doesn't subscribe to (refresh is full-history
within `oldest`).

## Gotchas

1. **Bot must be invited to private channels** — public channels work
   after install + correct scopes; private channels each require
   `/invite @your-bot`.
2. **`channel_types` default is public only**. To index DMs / private
   channels you also need `im:history` / `mpim:history` /
   `groups:history` scopes, and Slack adds an admin-approval step in
   workspaces with security policies.
3. **Long threads automatic sub-chunking**: `thread_aggregate` with text
   over 1500 chars is split at message boundaries; each sub-chunk gets
   `chunk_index: 0..N-1` + `msg_range: [start, end]` in its locator.
   When `cat --locator` is called with just `{thread_ts}` (no
   `chunk_index`), you get the **full thread** — sub-chunking is purely
   an embedding-time concern.
4. **Rate limit**: `conversations.history` is Tier 3 (~50 req/min). Big
   workspaces' initial syncs take time.
5. **No webhook live mode** — refresh via `mfs add --no-full`.
6. **User mentions in `text`** appear as `<@U0123>` — the connector does
   NOT currently expand those to display names. Add `users.jsonl` to the
   same search to resolve who's who.
