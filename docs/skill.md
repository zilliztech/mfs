# Agent Skill

MFS includes a reusable agent skill at:

```text
skills/mfs/
```

The skill teaches an agent when to use MFS search, when to browse with bounded
context, and when native shell tools are still the better choice.

## What the skill contains

```text
skills/mfs/
  SKILL.md
  references/
    command-reference.md
    workflow.md
    candidate-selection.md
```

- `SKILL.md` is the entry point: trigger guidance, decision tree, anti-patterns.
- `command-reference.md` explains command syntax and options.
- `workflow.md` explains the search-plus-browse loop.
- `candidate-selection.md` explains how to choose between close candidates.

The skill intentionally does not include scripts or platform-specific metadata.
It only needs shell access to the `mfs` command.

## Install for Codex

Codex reads user skills from `$HOME/.agents/skills` and repo-scoped skills from
`.agents/skills`.

User-level install:

```bash
mkdir -p ~/.agents/skills
cp -R skills/mfs ~/.agents/skills/mfs
```

Repo-scoped install:

```bash
mkdir -p .agents/skills
cp -R skills/mfs .agents/skills/mfs
```

Then invoke it explicitly with `$mfs`, or let Codex choose it when a task
matches the skill description.

## Install for Claude Code

Claude Code supports local skills in a `.claude/skills` directory.

```bash
mkdir -p .claude/skills
cp -R skills/mfs .claude/skills/mfs
```

Use the skill when asking Claude Code to search, inspect, or verify local
indexed files.

## Use with other shell-based agents

For agents without native skill discovery, provide `skills/mfs/SKILL.md` as a
system or developer instruction and make the files under
`skills/mfs/references/` available as supporting references.

The agent needs:

- shell access
- `mfs` on `PATH`
- an indexed target folder, or permission to run `mfs add <path>`

## Example prompts

```text
Use the MFS skill to find where PDF conversion cache eviction is implemented.
```

```text
Use MFS to answer this question from the local docs corpus. Verify the final
answer with file paths and line ranges.
```

```text
Use the MFS skill to search memory logs for the prior decision about queue
payloads, then inspect the surrounding context.
```

## Operating model

The skill follows the same model as MFS itself:

- use semantic search to locate candidates
- use progressive browse to understand structure
- use line ranges to verify exact evidence
- use native tools for exact literals and filename patterns

This keeps the agent from choosing between two poor extremes: reading whole
files too early or trusting a single search chunk without context.
