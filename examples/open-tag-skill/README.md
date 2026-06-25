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

## Install the skill

Open Tag ships as the `open-tag-admin` skill. It lives under `examples/`, so
install it with `--full-depth` (the default scan only covers top-level skills):

```bash
npx skills add zilliztech/mfs --full-depth --skill open-tag-admin -a claude-code -a codex -g
```

Claude Code and Codex are both supported.

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

This example is intentionally generic:

- no workspace-specific paths;
- no real Slack channel, user, team, or app IDs;
- no tokens or credentials;
- only placeholder connector URIs and environment variables.

## Prerequisites

Open Tag sits on top of a running MFS server with at least one indexed source:

1. **Install & run MFS** — `uv tool install mfs-server`, then `mfs-server run`
   (binds `127.0.0.1:13619`).
2. **Index a source** — Open Tag only consumes already-indexed scopes; it does not
   configure connectors. Use the **mfs-ingest** skill to add one (a local repo is
   the quickest start). MFS supports 20+ connectors — see `docs/connectors/`.

The runnable skill lives in [`open-tag/`](open-tag/). Start with:

```bash
cd examples/open-tag-skill/open-tag
python scripts/opentag_doctor.py --help
```
