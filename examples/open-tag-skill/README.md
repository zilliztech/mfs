# Open Tag Example

Open Tag is a small open-source demo of a Claude Tag-style Slack workflow built on
top of MFS: you mention a bot in Slack, the bot gathers authorized context, and a
CLI agent backend does the work. The official tag bot answers to `@Claude` — by
convention Open Tag answers to **`@OpenClaude`** (Claude backend) or
**`@OpenCodex`** (Codex backend), so it reads the same way.

The example maps the core concepts onto MFS:

- **Memory**: authorized Slack, repo, docs, issue, database, or object-store
  context indexed by MFS.
- **Tools**: external sources exposed through MFS connectors plus the workspace
  tools granted to the backend.
- **Brain**: a CLI agent backend — `claude -p` (Claude Code) or `codex exec`
  (Codex).

## Architecture

Open Tag is a thin layer of glue; all the retrieval power lives in MFS.

```text
       ┌──────────────┐
       │    Slack     │   @OpenClaude  <ask>
       │   (thread)   │ ◄──── answer ────┐
       └──────┬───────┘                  │
              │ mention                  │
              ▼                          │
   ┌──────────────────────────────────┐ │
   │         Open Tag  (glue)         ├─┘
   │   🧠 Brain = CLI agent backend   │
   │      claude -p  /  codex exec    │
   └────────────────┬─────────────────┘
                    │ scoped reads
                    ▼
   ┌──────────────────────────────────┐
   │               MFS                │
   │      🗄 Memory  +  🔧 Tools       │
   │   one searchable index over      │
   │   your data — Slack, repos,      │
   │   docs, DBs, files …             │
   └──────────────────────────────────┘
```

## See it in action

Two short demos — each is someone `@OpenClaude`-ing the bot in a Slack thread.

**Delegate a PR review across channels.** A teammate asked for a review in another
channel; from a different channel you tag `@OpenClaude` to handle it. The bot
reads the request from the other channel's history, pulls the PR through MFS, and
reports back — cross-channel context plus reaching an external source (GitHub).

![Open Tag — PR review delegation across channels](https://github.com/user-attachments/assets/6cb1db05-dd12-4a13-a9fa-1a1bf69bcf28)

**Follow up with a new, source-spanning task.** A follow-up in the same thread:
ask the bot to compare two projects and write up the differences. It gathers
context from the indexed sources and produces the document — a same-thread
follow-up that uses external data sources and tools.

![Open Tag — Slack follow-up that spans sources](https://github.com/user-attachments/assets/8f11e931-4248-46c5-b1fb-8128d56b8773)

Together they show what the **Memory + Tools** wiring buys you: from a single
mention, the bot can recall other channels' history and reach external sources
and tools.

## Install the skill

Open Tag ships as the `open-tag-admin` skill. It lives under `examples/`, so
install it with `--full-depth` (the default scan only covers top-level skills):

```bash
npx skills add zilliztech/mfs --full-depth --skill open-tag-admin -a claude-code -a codex -g
```

Claude Code and Codex are both supported.

## Quick start

Once the skill is installed, you drive everything from Claude Code or Codex in
plain language — the `open-tag-admin` skill handles credential setup, preflight,
and launch for you. Open your agent in a working directory and ask.

**No credentials yet?** You don't need any tokens in hand first — just say so and
the skill walks you through getting them. The full manual walkthrough (the Slack
app, the two kinds of Slack token, other sources) is under
[Credentials](#credentials) below, folded up. For example:

> I want to run an Open Tag bot but I don't have any Slack credentials yet. Walk
> me through creating the Slack app, turning on Socket Mode, and getting the bot
> and app tokens — tell me which scopes to add and where each token goes.

Once your credentials are ready — or if you already have them in your
environment — drive the rest in plain language:

**1. Give the bot some Memory — make your data searchable:**

> Make my context searchable before I wire up Slack. Index these into MFS:
> 1. my local repo at `/path/to/your/repo`,
> 2. the GitHub repo `your-org/your-repo` — code plus issues and PRs,
> 3. a few Slack channels, just `#eng`, `#support`, `#design`.
>
> The GitHub and Slack tokens are already in my environment, so reference them
> there instead of asking me to paste secrets. Tell me the object/chunk counts
> per source so I can confirm each one indexed.

**2. Stand up the bot:**

> Set up an Open Tag bot using the `claude` backend, listening in my Slack
> channel `#my-team-sandbox`. The Slack tokens are in my environment
> (`SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`). Run the preflight checks, then start
> the bridge once everything looks good.

**3. Inspect or adjust a running bot:**

> What is my Open Tag bot running right now — which backend, which Slack channel,
> and which MFS scopes can it search? Then add `linear://my-workspace` to what it
> is allowed to read.

Then go to Slack and `@OpenClaude` (or `@OpenCodex`) the bot inside a thread — it
gathers context from the permitted MFS scopes and replies in-thread.

## Credentials

Open Tag uses Slack credentials in **two** different places — don't confuse them:

| Credential | What it's for | Tokens |
|---|---|---|
| **Bridge app** | the bot that receives `@mentions`, reads the thread, posts replies | `SLACK_APP_TOKEN` (`xapp-…`, Socket Mode) **and** `SLACK_BOT_TOKEN` (`xoxb-…`) |
| **Slack-history connector** *(optional)* | indexing channel history into Memory so the bot can recall it | one token — **bot** (`xoxb-…`, recommended) or **user** (`xoxp-…`) |

The skill can do all of this from a plain-language ask. The manual walkthroughs
are here, folded, for when you'd rather do it yourself or want to see exactly
what's being requested. (The skill can guide and diagnose, but it can't bypass
workspace policy — if installing an app needs admin approval, an admin still has
to approve it.)

<details>
<summary><b>1. Create the Slack app + bridge tokens</b> (for receiving @mentions)</summary>

Go to <https://api.slack.com/apps> → **Create New App** → **From scratch**, and
name it for the backend (**OpenClaude** for `claude`, **OpenCodex** for `codex`).

![Slack Create New App button](https://github.com/user-attachments/assets/40ffd973-84d2-483f-beca-720c723223c2)
![Slack Create an app dialog](https://github.com/user-attachments/assets/5119276e-cde3-405e-bd86-7fb33b2218d9)
![Slack From scratch app form](https://github.com/user-attachments/assets/abbdc1cf-012a-44ed-ae62-210abc252980)

1. **Socket Mode** → enable it → create an app-level token with
   `connections:write`. Save it as `SLACK_APP_TOKEN` (`xapp-…`).
2. **OAuth & Permissions** → add Bot Token Scopes:
   - `app_mentions:read` — receive mention events
   - `chat:write` — post and update replies
   - `channels:read` + `channels:history` — read threads in public channels
   - `groups:read` + `groups:history` — private channels (optional)
3. **Event Subscriptions** → subscribe to the bot event `app_mention`.
4. **Install to Workspace** (reinstall after any scope/event change) → copy the
   **Bot User OAuth Token**. Save it as `SLACK_BOT_TOKEN` (`xoxb-…`).
5. Invite the bot to your sandbox channel: `/invite @OpenClaude`.

A private channel needs the bot to actually be a member, even with
`groups:history`. If preflight reports `not_in_channel`, invite it again.

</details>

<details>
<summary><b>2. Index Slack history into Memory</b> — bot token vs user token (optional)</summary>

This is separate from the bridge: it's the MFS **slack connector**, which indexes
channel history so the bot can search past conversations. You need **one** token:

- **Bot token** (`xoxb-…`, recommended) — same app as above; under **OAuth &
  Permissions** add `channels:read`, `channels:history`, `users:read` (plus
  `groups:*` for private channels), install, and copy the `xoxb-…` token. Invite
  the bot to any private channel you want indexed.
- **User token** (`xoxp-…`) — created the same way under **User Token Scopes**.
  Use it only when the bot identity can't reach what you can (DMs, channels the
  bot isn't in); always pair it with a channel allowlist so it doesn't index the
  whole workspace.

You don't write the connector config by hand — the **mfs-ingest** skill does,
keeping the token as an `env:` reference and bounding the channels. Full details
and screenshots: [`docs/connectors/slack.md`](../../docs/connectors/slack.md).

</details>

<details>
<summary><b>3. Other data sources</b> (GitHub, Postgres, Linear, …)</summary>

Each connector has its own credential, and the **mfs-ingest** skill walks you
through getting each one — what's needed and where to obtain it (a GitHub PAT, a
Postgres DSN, an OAuth token, …) — then stores it as an `env:` / `file:`
reference, never inline. Open Tag itself only *consumes* what MFS has indexed.

The full per-connector walkthroughs (20+ sources) are in
[`docs/connectors/`](../../docs/connectors/).

</details>

## Where it stands vs. a hosted tag bot

What Open Tag leans on — and where its edge is — is **Memory breadth**: MFS exposes
20+ connectors, including raw data layers (Postgres / Mongo / BigQuery / S3),
trackers (GitHub / Jira / Linear), chat, and local files, all **self-hosted**, so
your data and credentials never leave your machines. A hosted tag bot wins on the
things a demo deliberately skips: managed zero-ops, enterprise governance
(approvals, audit, spend limits), an ambient proactive mode, and an org-level
identity model.

So this is a demo/reference implementation, **not a production security
boundary**. It has no hardened sandbox, multi-user policy engine, audit system, or
approval flow. Anyone who can mention the bot can drive the backend, which runs
with your shell and environment — use it in an **isolated Slack channel on a
non-production machine** while adapting the pattern.

## What the skill sets up for you

You don't run any of this by hand. From the plain-language asks above, the
`open-tag-admin` skill drives the whole chain on your machine:

- gets the MFS server running on `127.0.0.1:13619` (installing it if needed);
- indexes the sources you name into MFS — the bot's searchable **Memory**
  (the indexing itself is handled by the **mfs-ingest** skill);
- runs the preflight checks and starts the Slack bridge.

What you bring is what an agent can't do for you:

- a machine to run on — the backend runs locally with your shell and environment;
- a Slack workspace where you can create and install an app, or an admin who can
  approve it;
- access to whatever data you want indexed.

The runnable code is in [`open-tag/`](open-tag/) — you can ignore it; the skill
handles everything. But if you'd rather see (or run) the steps yourself:

<details>
<summary>The manual sequence the skill automates</summary>

```bash
# 1. install + run MFS
uv tool install mfs-server && mfs-server run            # binds 127.0.0.1:13619

# 2. index your Memory (or just let the mfs-ingest skill do it)
mfs add /path/to/your/repo
mfs add slack://team-memory --config ./slack.toml       # optional Slack history

# 3. point the bot at the permitted scopes + Slack app + backend
export MFS_ALLOWED_SCOPES="file://local/path/to/your/repo,slack://team-memory"
export SLACK_APP_TOKEN="xapp-…"  SLACK_BOT_TOKEN="xoxb-…"
export OPENTAG_BACKEND="claude"  SLACK_CHANNEL_ID="C0…"
export MFS_URL="http://127.0.0.1:13619"  MFS_TOKEN="$(cat ~/.mfs/server.token)"

# 4. preflight, then start the bridge
cd examples/open-tag-skill/open-tag
python scripts/opentag_doctor.py --channel-id "$SLACK_CHANNEL_ID"
uv run --with slack-bolt python scripts/slack_socket_agent.py --backend "$OPENTAG_BACKEND"
```

Each step is documented in [`open-tag/references/`](open-tag/references/) (Slack
adapter, backends, runtime agent, memory).

</details>
