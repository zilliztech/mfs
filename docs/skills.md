# Agent Skills

MFS ships two agent-facing skill directories. They are meant to keep search/read
work separate from source registration and re-indexing work, because those tasks
have different side effects.

Use this page when you are deciding which skill to expose to an agent, or when an
agent needs a quick routing reminder before running `mfs` commands.

## Decision Table

| User intent | Use | Boundary | Current command shapes to expect |
|---|---|---|---|
| Search, grep, list, browse, read, reopen a search hit, or inspect exact evidence from a source that is already in MFS | [`mfs-find`][mfs-find-skill] | Read-only retrieval and browse work | `mfs search QUERY PATH`, `mfs search QUERY --all`, `mfs grep PATTERN PATH`, `mfs ls PATH --json`, `mfs cat PATH --range A:B`, `mfs cat PATH --locator JSON` |
| Add a new local path or connector URI | [`mfs-ingest`][mfs-ingest-skill] | Mutating MFS metadata, artifacts, jobs, and indexes | `mfs add TARGET --config FILE`, `mfs add TARGET`, `mfs add TARGET --upload` |
| Change an existing connector's TOML, credentials, object rules, or scope | [`mfs-ingest`][mfs-ingest-skill] | Mutating connector config and usually queueing a sync job | `mfs connector update TARGET --config FILE`, then `mfs job show JOB_ID` |
| Re-sync or force a full re-index | [`mfs-ingest`][mfs-ingest-skill] | Mutating index state and potentially re-billing embeddings | `mfs add TARGET`, `mfs add TARGET --since VALUE`, `mfs add TARGET --force-index`, or `mfs add TARGET --full` |
| Search returns weak or empty results | Start with [`mfs-find`][mfs-find-skill] | Diagnose query, scope, browse state, and job state before changing ingest | `mfs connector inspect TARGET`, `mfs ls PATH --json`, `mfs job list`, `mfs job show JOB_ID` |
| The diagnosis points to bad connector config, missing credentials, zero indexed objects, or a required re-sync | Switch to [`mfs-ingest`][mfs-ingest-skill] | Mutating follow-up after a read-only diagnosis | `mfs connector probe TARGET --config FILE`, `mfs connector update TARGET --config FILE`, `mfs add TARGET` |
| The user only asks what is registered | Either skill, depending on purpose | Inventory for query scope is read-only; inventory before add/update is ingest planning | `mfs status`, `mfs connector list`, `mfs connector inspect TARGET` |

## Routing Workflow

```text
User request
  |
  +-- Need existing content, exact evidence, or path browsing?
  |     -> use mfs-find
  |        -> search / grep / ls / cat / head / tail / export
  |
  +-- Need to register, update, remove, re-sync, or force-index a source?
  |     -> use mfs-ingest
  |        -> probe / add / connector update / job inspection
  |
  +-- Search failed or looks incomplete?
        -> start in mfs-find with read-only diagnostics
        -> switch to mfs-ingest only after the cause is connector config,
           credentials, missing ingest, or re-indexing
```

The practical rule: `mfs-find` may inspect state, but it should not run commands
that create, update, remove, or re-index sources. `mfs-ingest` may run mutating
commands, but it should confirm destructive or high-cost operations before doing
so.

## Current CLI Contract

Agents should use the current v0.4 command forms below. These replace stale
v0.3/v0.4-beta examples that may still appear in old prompts or notes.

| Do not use | Use now | Why |
|---|---|---|
| `mfs server-info` | `mfs status` for the status envelope; `mfs config show` for endpoint, profile, client id, and server info debugging | The Rust CLI has no `server-info` subcommand. |
| `mfs status TARGET` or `mfs status <uri>` | `mfs connector inspect TARGET` for one connector; `mfs ls PATH --json` for per-entry search state | `mfs status` has no path or URI argument. |
| `mfs connector ls` | `mfs connector list` | `list` is the current connector subcommand. |
| `mfs job ls`, `mfs job get JOB_ID`, `mfs job logs JOB_ID`, `mfs job ls --tail JOB_ID` | `mfs job list`, `mfs job show JOB_ID`, `mfs job cancel JOB_ID`; poll `job show` when you need progress | The current CLI has no job logs or tail subcommand. |
| `mfs add TARGET --estimate` | `mfs add TARGET --config FILE` without `--yes` for external connectors, or call `POST /v1/connectors/estimate` directly from an API client | There is no standalone estimate flag. External `mfs add` runs the zero-billing estimate and confirmation automatically unless `--yes` is set. |
| `mfs add TARGET --update` | `mfs connector update TARGET --config FILE` | Config updates use the connector update CLI path, which sends `update: true` to `/v1/add`. |

## Handoff Points

| From | To | Handoff signal |
|---|---|---|
| `mfs-find` | `mfs-ingest` | The source is not registered, `mfs connector inspect TARGET` shows no useful object/chunk state, `mfs ls PATH --json` shows entries that should be indexed but are not, or the user needs a config change. |
| `mfs-ingest` | `mfs-find` | A job succeeds and the user now wants to search, compare, summarize, or reopen evidence. |
| Either skill | User | The workflow requires source credentials, destructive removal, broad full re-indexing, or an unsupported command such as job log tailing. |

## Related Guides

- [Search and Browse](search-and-browse.md) for the search-to-read loop,
  locator reopening, JSON output, and weak-result recovery.
- [Connectors](connectors.md) for connector selection, lifecycle commands, TOML
  conventions, credential references, and the connector API map.
- [CLI Reference](cli.md) for exact command shapes, global `--json`, jobs,
  connector management, profiles, config, and local server commands.
- [Troubleshooting](troubleshooting.md) for endpoint/auth issues, upload mode,
  job inspection, empty search results, connector failures, and canonical error
  recovery.

[mfs-find-skill]: https://github.com/zilliztech/mfs/blob/main/skills/mfs-find/
[mfs-ingest-skill]: https://github.com/zilliztech/mfs/blob/main/skills/mfs-ingest/
