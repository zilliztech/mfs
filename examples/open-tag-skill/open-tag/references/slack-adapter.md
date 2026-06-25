# Slack Adapter

Use this reference when setting up the Slack-facing side of Open Tag from
scratch. The bridge is intentionally thin. It only:

1. Receives `app_mention` events through Socket Mode.
2. Reads the current thread through Slack Web API.
3. Posts one temporary working reply.
4. Runs `scripts/opentag_agent.py` with the selected CLI backend.
5. Replaces the working reply with the agent's final answer.

The adapter does not answer questions itself. It passes the thread, channel id,
and allowed MFS scopes to a fresh CLI agent.

The Slack app token and bot token are only for receiving mentions, reading the
current thread, and posting replies. Broader Slack memory should be configured as
an MFS Slack connector with its own token, channel allowlist, and source URI.

Relevant Slack docs:

- Socket Mode: <https://docs.slack.dev/apis/events-api/using-socket-mode/>
- App mentions: <https://docs.slack.dev/reference/events/app_mention/>
- OAuth scopes: <https://docs.slack.dev/reference/scopes/>

## Prerequisites

Before any Slack work, MFS must be running with at least one indexed source —
Open Tag only consumes already-indexed scopes:

1. `uv tool install mfs-server` → `mfs-server run` (binds `127.0.0.1:13619`;
   verify with `curl -s 127.0.0.1:13619/healthz`).
2. Index at least one source with the **mfs-ingest** skill (a local repo is the
   quickest start). See "MFS Memory Setup" below and `docs/connectors/`.

## End-To-End Checklist

1. Pick an isolated Slack channel, preferably private, for the first run.
2. Create a Slack app in the target workspace, named for the backend
   (**OpenClaude** for `claude`, **OpenCodex** for `codex`).
3. Enable Socket Mode and create an app-level token with `connections:write`.
   Save it as `SLACK_APP_TOKEN` (`xapp-...`).
4. Add bot scopes, install the app, and save the bot token as
   `SLACK_BOT_TOKEN` (`xoxb-...`).
5. Subscribe the app to `app_mention` bot events.
6. Invite the bot to the sandbox channel.
7. Configure MFS sources for Memory and set `MFS_ALLOWED_SCOPES`.
8. Choose a Brain backend with `OPENTAG_BACKEND`.
9. Run `scripts/opentag_doctor.py --channel-id <channel-id>`.
10. Start the Socket Mode bridge and mention the bot in Slack.

If the workspace blocks app creation or install approval, the user must ask a
Slack workspace admin to approve the app. The skill can guide the setup and
diagnose failures, but it cannot bypass workspace policy.

## Slack App Setup

Create or reuse a Slack app:

1. Go to <https://api.slack.com/apps>.
2. Create a new app from scratch in the target workspace. Name it for the chosen
   backend so the mention reads like the official `@Claude` tag: **OpenClaude**
   (`claude`) or **OpenCodex** (`codex`). The name is cosmetic — Open Tag strips
   the mention regardless.
3. Open **Socket Mode**, enable it, and create an app-level token with:
   - `connections:write`
4. Open **OAuth & Permissions** and add Bot Token Scopes:
   - `app_mentions:read` — receive bot mention events.
   - `chat:write` — post and update Slack replies.
   - `channels:read` + `channels:history` — read threads in public channels.
   - `groups:read` + `groups:history` — read threads in private channels.
5. Open **Event Subscriptions** and subscribe to Bot Events:
   - `app_mention`
6. Install or reinstall the app to the workspace after changing scopes/events.
7. Copy the **Bot User OAuth Token** (`xoxb-...`).
8. Invite the bot to the sandbox channel:
   ```text
   /invite @your-bot-name
   ```

For a private channel, bot membership matters even when the app has
`groups:history`. If `opentag_doctor.py` reports `not_in_channel`, invite the bot
again or use a channel where the bot is present.

## MFS Memory Setup

Use MFS to expose external context as Memory. Keep the allowed scope narrow for a
Slack demo.

Minimal local workspace source:

```bash
mfs add /path/to/workspace
export MFS_ALLOWED_SCOPES="file://local/path/to/workspace"
```

Slack history source:

```toml
# /tmp/opentag-slack.toml
token = "env:SLACK_MEMORY_TOKEN"
channel_types = ["public_channel", "private_channel"]
channel_names = ["team-demo-channel"]
include_unjoined = true
oldest = "now-30d"
max_read_rows = 50000
```

```bash
export SLACK_MEMORY_TOKEN="xoxb-or-xoxp-..."
mfs add slack://team-memory --config /tmp/opentag-slack.toml
export MFS_ALLOWED_SCOPES="slack://team-memory,file://local/path/to/workspace"
```

### More sources

Open Tag's reach is whatever MFS has indexed plus what you list in
`MFS_ALLOWED_SCOPES`. Add each once with **mfs-ingest** (it handles credentials),
then append its root to the scope list:

```bash
mfs add github://your-org/your-repo --config ./github.toml   # code + issues
mfs add linear://your-workspace     --config ./linear.toml   # issues
mfs add postgres://prod             --config ./pg.toml        # rows as objects
export MFS_ALLOWED_SCOPES="slack://team-memory,github://your-org/your-repo,linear://your-workspace,file://local/path/to/workspace"
```

Do not hand-write connector TOML here — Open Tag is only the consumer. For the
full connector list and per-connector credentials, use the **mfs-ingest** skill
and `docs/connectors/`.

Use a bot token for channels the bot can join. Use a user token only when the
demo intentionally needs the user's own visible Slack context, and always pair it
with `channel_ids` or `channel_names` to avoid indexing the entire workspace.

## Environment

Required:

```bash
export SLACK_APP_TOKEN="xapp-..."
export SLACK_BOT_TOKEN="xoxb-..."
export MFS_URL="http://127.0.0.1:13619"
export MFS_TOKEN="$(cat ~/.mfs/server.token)"
export MFS_ALLOWED_SCOPES="slack://team-memory,github://owner/repo,file://local/path/to/workspace"
export OPENTAG_BACKEND="<backend>"   # claude | codex
export OPENTAG_WORKDIR="/path/to/workspace"
export SLACK_CHANNEL_ID="<channel-id>"
```

The bridge does not need a model API key. The selected CLI backend handles model
auth and tool execution.

Optional:

```bash
export OPENTAG_MEMORY_ROOT="$HOME/.mfs/opentag-memory"
export OPENTAG_TIMEOUT_SECONDS=420
export OPENTAG_BACKEND_ATTEMPTS=3   # codex backend: retries on capacity/rate-limit
```

## Preflight

Run this before starting the bridge:

```bash
python scripts/opentag_doctor.py --channel-id "$SLACK_CHANNEL_ID"
```

The doctor checks:

- required environment variables are present;
- Slack bot token authenticates;
- the bot can see the target channel and read its history;
- MFS is reachable and each allowed scope can be listed;
- the selected backend is available.

Do not proceed until all checks pass. A failed MFS scope usually means the source
is not registered, not indexed yet, or the URI does not match the registered
connector root. A failed Slack channel check usually means missing scopes, app
not reinstalled after scope changes, or bot not invited to the channel.

## Run

From the skill directory:

```bash
uv run --with slack-bolt python scripts/slack_socket_agent.py --backend "$OPENTAG_BACKEND"
```

Then mention the bot in Slack:

```text
@your-bot-name Review the recent customer-facing discussion about the product issue, compare it with the repo docs, draft a short follow-up memo in the workspace, and reply with the file path plus open questions.
```

Follow-up messages in the same Slack thread are passed to the next backend run
through `conversations.replies`.

## Manual Non-Slack Test

Use this before the real Slack run if backend behavior is uncertain:

```bash
cat >/tmp/opentag-thread.txt <<'EOF'
U123: <@BOT> Review the recent customer-facing discussion about a product issue, compare it with the repository docs, draft a short follow-up memo in the workspace, and reply with the file path plus open questions.
EOF

python scripts/opentag_agent.py \
  --backend "$OPENTAG_BACKEND" \
  --channel-id "$SLACK_CHANNEL_ID" \
  --question "Review the recent customer-facing discussion about a product issue, compare it with the repository docs, draft a short follow-up memo in the workspace, and reply with the file path plus open questions." \
  --thread-file /tmp/opentag-thread.txt \
  --workdir /path/to/workspace
```

## Permission Notes

- Invite the Slack app only to channels where the bridge should respond.
- Keep broad Slack history access in MFS connector config, not in the Slack
  bridge. Use channel IDs or channel names to allowlist what the connector
  indexes.
- Treat `MFS_ALLOWED_SCOPES` as the runtime memory boundary. The helper scripts
  reject reads and searches outside those scopes.
- Use a sandbox Slack channel and a non-production workspace when testing
  backend command execution.
