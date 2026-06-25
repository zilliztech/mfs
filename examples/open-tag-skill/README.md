# OpenTag Example

OpenTag is a small open-source demo of a Claude Code Tag-style Slack workflow
built on top of MFS. The example name is **OpenTag**: a tag-in agent pattern where
you mention a bot in Slack, the bot gathers authorized context, and a CLI agent
backend does the work.

The example maps the core concepts onto MFS:

- **Memory**: authorized Slack, repo, docs, issue, database, or object-store
  context indexed by MFS.
- **Tools**: external sources exposed through MFS connectors plus the workspace
  tools granted to the backend.
- **Brain**: a CLI agent backend such as `codex exec`, `claude -p`, or a custom
  command.

This is a demo/reference implementation, not a production security boundary. It
does not provide a hardened sandbox, multi-user policy engine, audit system, or
enterprise approval flow. Use it in an isolated Slack channel and trusted
workspace while adapting the pattern.

This example is intentionally generic:

- no workspace-specific paths;
- no real Slack channel, user, team, or app IDs;
- no tokens or credentials;
- only placeholder connector URIs and environment variables.

The runnable skill lives in [`open-tag/`](open-tag/). Start with:

```bash
cd examples/open-tag-skill/open-tag
python scripts/opentag_doctor.py --help
```
