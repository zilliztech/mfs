---
name: mfs
version: 0.4.0
mfs_compat: ">=0.4,<0.5"
description: Use this skill to search, browse, and read across large, indexed multi-source collections — codebases, docs, PDFs, images, web crawls, GitHub repos, and databases (Postgres/MySQL/Mongo) — through the MFS shell-native CLI. MFS adds the most value on LARGE collections: it builds a hybrid (semantic + keyword) index so search is fast and recall is high, then you locate the exact spot and browse nearby. For tiny collections, plain grep/read is usually enough and MFS adds little.
---

# MFS — Multi-source File-like Search

MFS is a shell-native retrieval layer over many sources. One CLI (`mfs`) talks
to a server that indexes content into a hybrid (dense + BM25) index and exposes
POSIX-style verbs: `ls / tree / cat / head / tail / grep / search / export`.

**When MFS earns its keep — large collections.** The index makes search fast and
high-recall over thousands of files / rows / pages. The core play is:

> **search → locate → browse**: search to find *where*, read the line/record
> range to confirm *what*, browse nearby only as needed.

**When to just use shell.** For a handful of files, or an exact known string in a
known file, plain `grep` / `rg` / file read is simpler — MFS's index adds little
incremental value there. Use the smallest tool that answers the question.

---

## 1. Prepare the environment first (do this before relying on MFS)

**a) Check the CLI exists and matches this skill's version.**

```bash
mfs --version
```

- If `mfs` is missing: install the CLI (`uv tool install mfs` or `brew install zilliztech/tap/mfs`), then re-check.
- **Version alignment**: this skill targets `mfs_compat: >=0.4,<0.5` (see frontmatter). If `mfs --version` is outside that range, note it — core commands likely still work, but if something behaves oddly, update whichever side lags:
  - update CLI: `uv tool upgrade mfs` (or `brew upgrade mfs`)
  - update server: `uv tool upgrade mfs-server`
  Only update when the task needs it or the user asked; otherwise proceed and keep the mismatch in mind.

**b) Make sure a server is reachable.**

```bash
mfs status            # server up? connectors? index freshness? jobs?
```

If no local server is running and you're on a personal machine: `mfs serve start`
(needs the `mfs-server` package — `uv tool install mfs-server`). On a remote/team
profile the server is already hosted; just point your profile at it.

**c) Make sure the target is indexed (only if you need `search`).**

- `mfs search` needs an index → run `mfs add <path-or-uri>` first if not indexed.
- `mfs grep` works without an index (connector pushdown, or BM25 if indexed, or linear scan fallback).
- `mfs ls / tree / cat / head / tail` browse without an index.
- `mfs status <uri>` shows per-connector search availability (`available / partial / building / unavailable`) and per-object `search_status`.
- For a large source, prefer asking before kicking off a big `mfs add` unless the user already requested it. `mfs add` is async — poll `mfs status <uri>` until search is `available`.

---

## 2. The core workflow (large collections): search → locate → browse

Detailed playbook in **[references/workflow.md](references/workflow.md)**. In short:

1. **Search** for candidates (this is where the index pays off):
   ```bash
   mfs search "<natural-language intent>" <path-or-uri> --top-k 10
   ```
   Modes: default `hybrid` (dense+BM25), `--mode semantic` (pure dense), `--mode keyword` (pure BM25).
2. **Locate** from the result envelope:
   - text/code hit → has `lines [start,end]` → `mfs cat <source> --range start:end`
   - structured hit (DB row / issue / thread) → has `locator` (pk/number/thread_ts) → `mfs cat <source> --locator '{...}'`
3. **Browse** nearby to confirm context: `mfs cat --peek/--skim`, `mfs head`, `mfs tree`.

For exact identifiers / error codes / config keys, use `mfs grep` (or plain `grep`/`rg`).
`mfs grep` precision varies by path: connector pushdown & linear scan are literal-exact;
the BM25 path (indexed objects) is token-level, not regex — for exact-exhaustive use a
pushdown source or `mfs export` + local `grep`.

---

## 3. Command map

| Need | Command |
|---|---|
| register + index a source | `mfs add <path-or-uri>` |
| semantic/keyword search | `mfs search "<q>" <path> [--mode hybrid\|semantic\|keyword] [--top-k N] [--all]` |
| keyword / full-text | `mfs grep "<pattern>" <path>` |
| browse structure | `mfs ls <uri>` / `mfs tree <uri> -L 2` |
| read object / range / single record | `mfs cat <uri> [--range A:B] [--locator '{...}'] [--meta]` |
| endpoints | `mfs head -n N <uri>` / `mfs tail -n N <uri>` |
| full export for offline processing | `mfs export <uri> <file>` |
| status / connectors / jobs | `mfs status [<uri>]`, `mfs connector ...`, `mfs job ...` |

Agents should prefer `--json` and read the stable envelope (`source / lines / content / score / locator / metadata`).

---

## 4. Route to the right reference

- **How to drive search→locate→browse, weak-result recovery, scoping** → [references/workflow.md](references/workflow.md)
- **What a given connector exposes** (paths, object names, locator shape, cat behavior, limits) → `references/connectors/<scheme>.md`. Read the one matching the URI scheme before guessing its layout. Available: `file`, `web`, `github`, `postgres`, `mysql`, `mongo`, `bigquery`, `snowflake`, `s3`, `gdrive`, `slack`, `discord`, `gmail`, `feishu`, `notion`, `jira`, `linear`, `zendesk`, `salesforce`, `hubspot`.
- **Result envelope fields & how to reopen a hit** → [references/json-envelope.md](references/json-envelope.md)
- **Error codes & recovery** → [references/error-codes.md](references/error-codes.md)

> Per-connector reference docs are generated at release time from each connector's
> `PROMPT.md`; agents read them as static context and don't fetch prose at runtime.
> Runtime capability is queried structurally via `mfs ls <uri> --json`
> (`capabilities`, `search_status`).
