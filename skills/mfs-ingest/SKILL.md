---
name: mfs-ingest
version: 0.4.0
mfs_compat: ">=0.4,<0.5"
description: Register, update, or re-sync data sources for MFS so they become searchable — postgres / mysql / mongo / snowflake / bigquery, github / jira / linear / notion / salesforce / hubspot / zendesk, slack / discord / gmail / feishu, s3 / gdrive / web / file. Use whenever the user wants to ADD a new data source to MFS, change an existing connector's config, re-ingest / re-index a source, list registered connectors, or troubleshoot a sync that's not picking up data. Trigger phrases include "add X to MFS", "ingest my [postgres/slack/github/etc]", "register this repo / database / workspace", "make X searchable", "re-sync Y", "update the slack token", "what connectors do I have". Do NOT use for: searching / finding / reading content (use `mfs-find`); raw mutation of the source itself (MFS only reads).
---

# MFS — register / update / re-sync data sources

## 1. What this skill does

Walks the user through getting a data source into MFS so it's searchable.
The work splits into:

1. Picking the right connector scheme.
2. Collecting credentials (preferring `env:VAR` / `file:/path` indirection
   over plaintext).
3. Writing a connector TOML.
4. Calling `mfs add <uri> --config <toml>` and monitoring the returned job.

Each connector has its own field set, credential acquisition story, and
gotchas. Per-connector details live in
`reference/connectors/<scheme>.md` — **read the matching one before
collecting fields** for any scheme.

## Step 0: Pre-flight (always run first)

```bash
mfs --version            # missing? `uv tool install mfs`
mfs status               # server reachable? connectors/jobs visible?
mfs config show          # endpoint/profile/client id/server-info debugging
mfs connector list       # what's already configured?
```

Branch on the result:

| Signal | Action |
|---|---|
| `mfs` not found | install: `uv tool install mfs` |
| `mfs status` connection refused | tell user: `uv tool install mfs-server && mfs-server setup && mfs-server run`. Cannot proceed without a server. |
| `mfs status` returns 401 unauthorized | the user's `MFS_API_TOKEN` is missing/wrong. Use `mfs config show` to confirm the endpoint/profile, then set the intended token source and retry. |
| server up + `connector list` empty | first-ever connector; jump to **§B (greenfield walk-through)** when intent matches |
| server up + N connectors registered | proceed to Step 1 intent classification |

## Step 1: Classify intent (the central decision)

Read the user's most recent message. Pick exactly one row:

| User said... | Intent | Jump to |
|---|---|---|
| "add postgres prod-db to MFS" + credentials available (env / file / about to paste) | **A. Zero-friction add** | §A |
| "I want to add postgres / slack / X" (no specifics, vague) | **B. Greenfield walk-through** | §B |
| "re-sync github", "re-index slack", "pull latest from jira" | **C. Force re-ingest** | §C |
| "update my slack token", "change postgres host", "switch to new DSN" | **D. Edit existing config** | §D |
| "what connectors do I have", "list registered sources" | **E. List** | §E |
| "find X" / "search Y" / "grep Z" / "cat W" | **wrong skill** | redirect to `mfs-find`, stop |
| "is X indexed yet" / "did the sync finish" / "search returns nothing" | **wrong skill or boundary** | suggest `mfs-find` for query-side diagnosis; if user says it's an ingest issue, jump to §C or §F |
| Truly unclear after a re-read | **F. Clarify** | §F |

### Mid-flow redirect

If at any point the user changes intent ("wait, just list what I have" /
"actually let me just re-sync the existing one"), abandon the current §
and jump to the new one. Don't insist on finishing the original branch.

---

## §A. Zero-friction add

User knows what to add and has credentials handy. Aim for: ≤3 questions
to the user, then write toml + run `mfs add`.

1. **Parse the URI** from the user's message. Shape: `<scheme>://<alias>`.
   Scheme is required and is the connector type (`postgres`, `slack`, …).
   Alias is the human-readable instance ID — gets used as the toml
   filename and the connector's row in metadata.
   - If only `<scheme>` was given (no alias), ASK: "What should I call
     this instance? (free-form; appears as the URI host part, e.g.
     `postgres://**prod-db**`)"

2. **Read the matching `reference/connectors/<scheme>.md`** for the
   required field set, then for each required field:
   - Check if a likely env var is set:
     ```bash
     env | grep -iE '<scheme>_DSN|<scheme>_TOKEN|<COMMON_NAME>'
     ```
     If found, suggest: "Use `env:VAR_NAME` (recommended — keeps secret
     out of the toml)?" — wait for yes/no.
   - Otherwise ASK for the value. For secrets, recommend `env:VAR` or
     `file:/path` form rather than pasting plaintext. See
     `reference/credentials.md` for the indirection syntax.

3. **Write the toml** to a temp path:
   ```toml
   # mfs-server connector config — <scheme>
   # URI: <uri>
   <field1> = "<value or env:VAR>"
   <field2> = "<value>"
   ...
   ```
   Use a path like `/tmp/mfs-<alias>.toml` so it doesn't pollute the
   user's cwd.

4. **Run `mfs add` in estimate-confirm mode** for external sources where
   cost matters (databases >100k rows, GitHub repos with many issues,
   large Slack workspaces, full website crawls):
   ```bash
   mfs add <uri> --config /tmp/mfs-<alias>.toml
   ```
   For non-local targets, the current CLI automatically calls
   `/v1/connectors/estimate` and prompts `Continue? [y/N]` unless `--yes`
   is set. There is no standalone `--estimate` flag. Show the estimate to
   the user and only answer yes when the user has approved.

   For small / unambiguous sources (single repo of docs, one CRM with
   <10k records, a defined Slack channel), the same command is still the
   normal add path. Use `--yes` only when the user has already accepted
   skipping the estimate confirmation.

5. **Capture the queued job id**. If the step 4 command was approved at the
   prompt, it already queued the job. For local targets, or when the user has
   explicitly approved skipping the estimate confirmation, run:
   ```bash
   mfs add <uri> --config /tmp/mfs-<alias>.toml
   ```
   Capture the returned `job_id`. `mfs add` always returns after queueing;
   use `mfs job show` or `mfs job list` to watch terminal state.

6. **Follow the job** until terminal state:
   ```bash
   mfs job show <job_id>
   # or polled:
   while true; do
     state=$(mfs job show <job_id> | jq -r .status)
     [ "$state" = succeeded ] || [ "$state" = failed ] && break
     sleep 5
   done
   ```

7. **Confirm result**:
   - `succeeded` + `succeeded_objects > 0` → tell user it's ready, give
     one example: "Try: `mfs search '<sample query>' <uri>`".
   - `succeeded` + `succeeded_objects == 0` → check `mfs ls <uri>` —
     either source genuinely empty, or wrong `text_fields`/scope.
     Read `reference/troubleshooting.md`.
   - `failed` → read the job's `error` field, match against
     `reference/troubleshooting.md`, propose a fix and ask user.

---

## §B. Greenfield walk-through

User vague about what to add. Hand-hold through scheme picking, then
delegate to §A's steps 2-7 with the chosen scheme.

1. **Pre-flight** (Step 0 already covered this).

2. **Ask: which kind of source?** Group the 20 schemes by shape so the
   choice is tractable:
   ```
   Pick the source TYPE:
     1. Database tables       (postgres, mysql, snowflake, bigquery)
     2. Document store        (mongo)
     3. Code repository       (github)
     4. Issue tracker / wiki  (jira, linear, notion)
     5. CRM                   (salesforce, hubspot)
     6. Support / help desk   (zendesk)
     7. Chat / messaging      (slack, discord, gmail, feishu)
     8. Cloud storage / files (s3, gdrive, file, web)
     9. Other (specify)
   ```
   Once user picks a group, narrow to the specific scheme (e.g. "Database
   tables → postgres / mysql / snowflake / bigquery — which?").

3. **Ask for an instance alias** (host part of the URI; e.g. "prod-db",
   "support-workspace", "main-repo").

4. **Read `reference/connectors/<scheme>.md`** — its top section
   "How to obtain credentials" guides the user through fetching the
   token/DSN/key from the source's own console. Walk them through one
   step at a time, ask after each step ("Got the token? Paste it as
   `env:VAR_NAME` if it's already exported, or paste the value here").

5. **Continue with §A from step 2** (collect fields → write toml →
   estimate-confirm/add → follow job → confirm).

---

## §C. Force re-ingest

User wants to re-sync an existing connector — typically because the
source changed (new tickets, new PRs, new files) and the user doesn't
want to wait for the next scheduled sync.

1. **Confirm the URI** matches a registered connector:
   ```bash
   mfs connector list | grep <alias-or-scheme>
   ```
   If not found, redirect to §B.

2. **Confirm with the user** when it's a force-full re-index (re-embeds
   everything, costs tokens):
   > "Re-syncing `<uri>`. Pick one:
   >   • no flag     pull changed data using the connector's normal sync path
   >   • `--since`   ask connectors with a time cursor to limit the sync
   >   • `--full`    re-embed everything from scratch (re-bills embedding
   >                 API; only do this if you've changed `text_fields`,
   >                 the embedding model, or chunking config)"

3. **Run**:
   ```bash
   mfs add <uri>                  # incremental: re-uses existing toml + caches
   mfs add <uri> --full           # full re-embed
   mfs add <uri> --since <date>   # only new content since date
   ```

4. **Follow + confirm** as in §A step 6-7.

---

## §D. Edit existing config

User wants to change a registered connector — new token, different
`text_fields`, more channels, raise `max_read_rows`, etc.

1. **Locate the existing toml**:
   ```bash
   ls -la $MFS_HOME/connectors/<alias>.toml
   # OR (if MFS_HOME unset)
   ls -la ~/.mfs/connectors/<alias>.toml
   ```

2. **Read it** so the user sees current state. ASK what they want to
   change. Common edits and the right field:

   | Want to change | Field |
   |---|---|
   | Auth token | `token` / `api_key` / `access_token` (scheme-dependent) |
   | DSN / connection string | `dsn` / `uri` |
   | Which channels / projects / labels | `channels` / `projects` / `labels` (multi-value) |
   | Max records per object | `max_read_rows` |
   | Cap on per-object chunks | `chunk_max` |
   | Which columns to embed (DB / SaaS) | `[[objects]] text_fields` |
   | Make a previously-indexed object stop indexing | `[[objects]] indexable = false` |

   See `reference/connectors/<scheme>.md` for the full field list.

3. **Apply the minimum diff**. Don't rewrite unrelated fields.

4. **Run `mfs connector update`** so the engine applies the new config
   through the explicit update path:
   ```bash
   mfs connector update <uri> --config $MFS_HOME/connectors/<alias>.toml
   ```

5. **Follow + confirm** as in §A step 6-7.

### When does a config update re-embed?

| What changed | Re-embed? |
|---|---|
| auth token, DSN host | no — re-runs sync only |
| `max_read_rows` increased | partial — picks up newly visible records |
| `text_fields` (which columns become content) | yes — content shape changed |
| `embedding.*` in server config | yes (and affects ALL connectors) |
| `[[objects]] indexable = false` | drops that object from index |

If the change forces re-embedding and the source is large, ask the user before
running the update if the cost cannot be estimated from the CLI.

---

## §E. List registered connectors

```bash
mfs connector list                        # via the running server (live state)
mfs-server connector list                 # on-disk tomls under $MFS_HOME/connectors/
```

The two views can differ:
- `mfs connector list` shows what the server has registered in its
  metadata DB.
- `mfs-server connector list` shows the toml files on disk (admin spec).

If they diverge, the disk file is a saved spec the user can re-apply with
`mfs add <uri> --config <toml>`.

Format the output as a small table for the user. If they then ask about
one specific connector, switch to §C / §D as appropriate.

---

## §F. Clarify intent

User's first message was too vague to pick a path. Ask one short
question, default to §B if they shrug:

> "Want to (1) add a new source, (2) re-sync an existing one, (3) change
> the config of one you've already added, or (4) just see what's
> registered?"

After they answer, jump to the matching §.

---

## Useful commands (cross-cutting)

Cheap reads any path may need:

```bash
mfs status                       # server + all connectors at a glance
mfs config show                  # endpoint/profile/client id/server info
mfs connector inspect <uri>      # one connector's object/job summary
mfs ls <uri> --json              # per-entry search_status
mfs connector list               # live server view
mfs-server connector list        # on-disk toml view (admin)
mfs job list                     # recent ingest jobs
mfs job show <job_id>            # one job's state and error field
mfs job cancel <job_id>          # cancel a queued/running job
mfs remove <uri>                 # drop a connector + its index data (DESTRUCTIVE)
```

`mfs remove <uri>` permanently removes that connector AND its indexed
chunks from Milvus. ALWAYS confirm with the user before running it.

## Anti-patterns to flag back to the user

- **Adding the same URI twice** — second `mfs add <uri>` updates the
  existing connector, doesn't create a duplicate. If the user really
  wants two postgres instances, give them different aliases
  (`postgres://prod-db` vs `postgres://staging-db`).
- **Cosmetically-different URIs that point to the same source** — the
  URI string IS the connector identity; MFS does NOT canonicalize
  across the scheme-specific forms a host/database can take. So
  `postgres://h:5432/db` and `postgres://h/db` register as two
  separate connectors over the same physical DB, each with its own
  job queue + collection state. Pick one form per source and stick
  with it; if the user is mid-flow and you spot the drift, suggest
  rolling back the duplicate with `mfs connector remove`.
- **Pasting plaintext tokens into the toml when an env var exists** —
  suggest `env:VAR` form, especially when the user mentions docker /
  K8s / CI / shared host.
- **Setting `text_fields` blindly on a SaaS connector** — most have a
  built-in preset (see `reference/connectors/<scheme>.md` for what auto-
  applies); user only needs `[[objects]]` for overrides.
- **`--full` re-embedding to "fix" a search problem** — wastes tokens.
  First diagnose with `mfs-find` (§12) whether the issue is index
  config or query construction.

## Reference routing

- **[`reference/credentials.md`](reference/credentials.md)** — WHEN about
  to write a secret value into a toml. Covers `env:VAR` and `file:/path`
  indirection syntax, security tradeoffs, and which env vars common SaaS
  CLIs already export.

- **[`reference/update-flow.md`](reference/update-flow.md)** — WHEN
  walking §D and the user wants to change something that has knock-on
  consequences (changing `text_fields`, switching embedding provider).

- **[`reference/troubleshooting.md`](reference/troubleshooting.md)** —
  WHEN `mfs add` failed, job state is `failed`, or `succeeded` but
  `succeeded_objects == 0`. Maps common error messages to recovery steps.

- **[`reference/error-codes.md`](reference/error-codes.md)** — WHEN an
  `mfs` command returned `--json` error output with a `code` field.

- **`reference/connectors/<scheme>.md`** — WHEN about to add or update a
  specific scheme. REQUIRED reading before the credential-gathering
  step. Schemes: `postgres`, `mysql`, `mongo`, `snowflake`, `bigquery`,
  `slack`, `discord`, `gmail`, `feishu`, `github`, `jira`, `linear`,
  `notion`, `salesforce`, `hubspot`, `zendesk`, `s3`, `gdrive`, `web`,
  `file`.

The per-connector files cover credential acquisition, required toml
keys, optional knobs, and known pitfalls. Don't guess any of these from
training-data memory — the reference is the source of truth for this
codebase's connectors.
