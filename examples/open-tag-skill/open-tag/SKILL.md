---
name: open-tag
description: Create, run, test, and maintain an OpenTag Slack mention workflow backed by MFS and interchangeable CLI agent backends. Use when setting up a tag-in agent, choosing MFS data-source scopes, configuring Slack Socket Mode tokens, selecting a CLI backend such as codex exec, claude -p, or a custom command, starting or debugging the bridge, managing permitted context, or validating thread context, retrieval, and task execution.
---

# OpenTag

Use this skill to set up and operate an open Slack tag-in assistant. Keep the
architecture generic:

- **Brain**: the selected CLI agent backend, such as `codex exec`,
  `claude -p`, or an operator-provided command.
- **Memory**: MFS-indexed, operator-authorized context such as Slack history,
  repositories, docs, issues, databases, or object stores.
- **Tools**: MFS connectors for external read/search plus any explicit tools the
  backend is allowed to use in the workspace.

The user-facing flow is:

1. Configure MFS sources and allowed scopes.
2. Configure a Slack app with Socket Mode and invite it to a sandbox channel.
3. Start the OpenTag bridge.
4. Move to Slack and tag the bot in a thread.
5. Let the bridge invoke the selected backend with thread context and scoped MFS
   helper scripts for permitted external context.

This skill does not call a model API directly. Model access, tool access, and
write permissions come from the selected CLI agent backend.

## Setup Workflow

1. Read `references/slack-adapter.md` and follow its end-to-end checklist.
2. Confirm or create a private or otherwise isolated Slack channel.
3. Create or reuse a Slack app, enable Socket Mode, subscribe to `app_mention`,
   add the required bot scopes, install it, and invite the bot to the channel.
4. Configure MFS memory sources and set `MFS_ALLOWED_SCOPES` to the exact source
   roots the runtime agent may use.
5. Choose `OPENTAG_BACKEND` explicitly: `codex`, `claude`, or `custom`.
6. Run `python scripts/opentag_doctor.py --channel-id <channel-id>` and fix any
   failed check.
7. Start the bridge with
   `uv run --with slack-bolt python scripts/slack_socket_agent.py --backend <backend>`.
8. Validate thread context, permitted-context retrieval, and task execution with
   a realistic delegated task.

## What The Python Scripts Do

Keep the Python scripts as deterministic glue:

- `slack_socket_agent.py`: receive Slack `app_mention`, read the thread, post
  progress, call the backend, and post the final answer.
- `opentag_agent.py`: build a non-interactive prompt and invoke the selected CLI
  backend.
- `mfs_search.py` and `mfs_cat.py`: call the MFS HTTP API with scoped search and
  reads.
- `opentag_memory.py`: maintain optional local seed notes and re-index them in
  MFS for deterministic demos.
- `opentag_doctor.py`: preflight environment variables, Slack bot access, MFS
  reachability, allowed scopes, and backend availability.

Shell scripts can wrap these commands, but Python is less brittle for Slack Web
API calls, JSON handling, temporary files, subprocess timeouts, and cross-agent
backend selection.

## Runtime Contract

The Slack bridge invokes a fresh CLI agent per mention. That runtime agent must
follow `references/runtime-agent.md`. Keep runtime behavior there, not in this
setup skill.

Thread context is short-term state. Durable context should come from permitted
MFS scopes such as indexed Slack history, repos, docs, issues, databases, object
stores, or optional local seed notes. See `references/memory.md` for the optional
helper's file shape.

Never hard-code real workspace names, channel IDs, user IDs, local absolute
paths, or customer/project details into this skill. Use placeholders in
documentation and environment examples.

## References

- Read `references/slack-adapter.md` when configuring or running Slack.
- Read `references/backends.md` when changing backend selection or command
  invocation.
- Read `references/runtime-agent.md` when changing per-mention behavior.
- Read `references/memory.md` only when using optional local seed notes.
