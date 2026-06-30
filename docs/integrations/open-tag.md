# Open Tag — a Slack tag-in bot

[Open Tag](https://github.com/zilliztech/mfs/tree/main/examples/open-tag-skill)
is a small open-source demo of a **Claude Tag-style** Slack workflow built on
MFS: you `@mention` a bot in a thread, it gathers authorized context, and a CLI
agent backend does the work. By convention it answers to **`@OpenClaude`**
(Claude backend) or **`@OpenCodex`** (Codex backend), so it reads like the
official `@Claude` tag.

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

It maps the three parts onto MFS:

- **Memory** — the MFS scopes you have indexed and allow (`MFS_ALLOWED_SCOPES`).
- **Tools** — MFS connectors for external read/search, plus the backend's own
  workspace tools.
- **Brain** — a CLI agent backend: `claude -p` (Claude Code) or `codex exec`
  (Codex).

Its edge over a hosted tag bot is **Memory breadth**: every connector — databases,
object stores, trackers, chat, local files — can be permitted context, all
self-hosted, so your data and credentials never leave your machines.

## Quick start

Open Tag ships as the `open-tag-admin` skill. Install it, then drive everything
from Claude Code or Codex in plain language — the skill handles credential setup,
preflight, and launch for you.

```bash
npx skills add zilliztech/mfs --full-depth --skill open-tag-admin -a claude-code -a codex -g
```

**No credentials yet?** You don't need any tokens in hand first — the skill walks
you through creating the Slack app and its tokens, and the per-source tokens for
Memory. For example, ask your agent:

> I want to run an Open Tag bot but I don't have any Slack credentials yet. Walk
> me through creating the Slack app, turning on Socket Mode, and getting the bot
> and app tokens — tell me which scopes to add and where each token goes.

Then, once credentials are ready:

> Make my context searchable, then stand up an Open Tag bot on the `claude`
> backend listening in my Slack channel `#my-team-sandbox`. Index my local repo
> and the GitHub repo `your-org/your-repo` (code + issues) into MFS first, run
> the preflight checks, and start the bridge.

Then go to Slack and `@OpenClaude` the bot inside a thread — it gathers context
from the permitted MFS scopes and replies in-thread.

The full walkthrough, scripts, and setup references live in the
[example directory](https://github.com/zilliztech/mfs/tree/main/examples/open-tag-skill).

## Not a production boundary

This is a reference pattern, not a production security boundary. The backend runs
with your shell and environment, and anyone who can mention the bot can drive it,
so use an **isolated Slack channel on a non-production host**. A hosted tag bot
wins on the things a demo deliberately skips: managed zero-ops, enterprise
governance (approvals, audit, spend limits), an ambient proactive mode, and an
org-level identity model.
