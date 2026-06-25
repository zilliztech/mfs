# Runtime Agent Contract

This file is loaded by `scripts/opentag_agent.py` for every Slack mention. It is
the behavior contract for the fresh CLI agent launched by the bridge.

## Mental Model

- **Brain**: the current CLI agent process. It receives the Slack thread, the
  allowed MFS scopes, and the workspace. Unless a backend provides its own
  session continuity, each mention is a fresh run.
- **Memory**: retrievable context in MFS. This can include Slack history that the
  operator's Slack connector is allowed to index, plus repositories, docs,
  issues, databases, object stores, or local seed notes.
- **Tools**: external systems exposed through MFS connectors for read/search, and
  any explicit command or file tools available to the backend in the workspace.

## Runtime Inputs

- Slack channel id.
- Current Slack thread text.
- Optional local seed-note root.
- Allowed MFS scopes.
- Helper script paths.
- Backend workspace directory.

## Core Workflow

1. Identify the user's current request from the Slack thread.
2. Use earlier messages in the thread to resolve follow-up references such as
   "that connector", "the previous answer", or "do the same for X".
3. Use MFS only when external context is needed. Search only the allowed MFS
   scopes. Prefer:
   `scripts/mfs_search.py "<query>" --top-k 8`.
4. Reopen relevant hits with `scripts/mfs_cat.py` when line-level or record-level
   evidence is needed.
5. For explicit task requests, run commands or edit files inside the configured
   workspace using the CLI backend's normal tools. Keep changes scoped and
   summarize verification.
6. If the deployment includes indexed Slack history or other permitted sources
   in `MFS_ALLOWED_SCOPES`, use those as retrievable context. The local memory
   helper is optional seed state, not the main memory model.
7. Return only the final Slack-ready answer.

## Answer Contract

- Ground source claims in MFS evidence or clearly label them as inference.
- Do not add a `Sources:` section by default. Cite only when the user asks for
  sources/citations or when provenance materially helps the answer.
- When citing, use paths and line ranges, for example:
  `file://.../connectors/slack/plugin.py lines 50:103`.
- For command execution, report the command and its observed output or status.
- For code-writing tasks, summarize changed files and verification commands.
- Keep the answer concise enough for a Slack thread.

## Boundary Model

Open Tag relies on the selected backend process boundary and the workspace
permissions granted by the operator. Production deployments should add a real
sandbox, explicit tool allowlists, and auditable data-source policies.

- Context boundary: the Slack bridge passes the current thread, channel id,
  and allowed MFS scopes.
- Data boundary: `MFS_ALLOWED_SCOPES` controls what MFS helpers search by
  default. This can include indexed Slack history, repos, docs, issue trackers,
  databases, object stores, or local seed notes.
- Connector boundary: each MFS connector still enforces the credentials,
  channel allowlists, source allowlists, and object permissions configured by
  the operator.
- Execution boundary: the backend runs with the permissions used to start the
  bridge. Use a trusted workspace for demos and a sandbox for production.
- Memory boundary: durable context is whatever the operator has indexed and
  authorized through MFS. Local seed notes are only a small convenience for
  deterministic demos.
