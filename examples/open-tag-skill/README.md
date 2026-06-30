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

**No credentials yet?** You don't need any tokens in hand first. The skill walks
you through obtaining them — creating the Slack app and its bot/app tokens for
the bridge, and the per-source tokens (GitHub, Slack history, databases, …) for
Memory, including which scopes to add and where each token goes. For example:

> I want to run an Open Tag bot but I don't have any Slack credentials yet. Walk
> me through creating the Slack app, turning on Socket Mode, and getting the bot
> and app tokens — tell me which scopes to add and where each token goes.

(It can guide the setup and diagnose failures, but it can't bypass workspace
policy — if your workspace requires admin approval to install an app, you'll
still need an admin to approve it.)

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

Want to look under the hood? The runnable code is in [`open-tag/`](open-tag/) and
the connector catalog is in [`docs/connectors/`](../../docs/connectors/) — but you
can ignore both; the skill handles them.
