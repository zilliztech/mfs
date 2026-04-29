# Agent Workflows

MFS is designed for agents that can run shell commands. The agent does not need
an SDK; it needs a small workflow.

For reusable setup, install the companion skill from `skills/mfs/`. See
[Agent Skill](skill.md) for installation and usage notes.

## General rule

Use MFS to locate and orient. Use native shell tools when they are the best
literal tool for the job.

Good pattern:

```bash
mfs tree --peek -L 2 .
mfs search "where are stale summaries tracked" . --top-k 5
mfs cat --skim ./src/mfs/search/summary.py
mfs cat -n 1:160 ./src/mfs/search/summary.py
```

## Code work

For codebases, combine semantic queries with exact identifiers.

```bash
mfs search "how does add decide queue priority" .
mfs grep "_task_priority" ./src
mfs cat --skim ./src/mfs/cli.py
mfs cat -n 120:220 ./src/mfs/cli.py
```

Use `mfs tree --peek` before broad edits. It helps avoid missing nearby modules
whose filenames do not match the query.

## Documentation work

For documentation corpora, wording often differs between the user question and
the relevant page.

```bash
mfs search "how do I publish a site from markdown docs" ./docs
mfs cat --skim ./docs/getting-started.md
mfs grep -C 2 "mkdocs" ./docs
```

Search finds paraphrases. Grep confirms exact commands or option names.

## Memory and transcript work

For memory logs, conversation transcripts, or daily notes:

```bash
mfs search "when did we decide not to store raw chunks in queue" ./memory
mfs grep -i "queue.json" ./memory
mfs cat --skim ./memory/2026-04-29.md
```

This is where the two-leg model is especially useful: semantic search finds the
episode, browse recovers the surrounding decision.

## JSON output

Use `--json` when the caller is a program.

```bash
mfs search "pdf cache eviction" . --json
mfs cat --skim ./docs/formats.md --json
```

The output uses one Hit envelope shape across `search`, `grep`, `ls`, `tree`,
and `cat`: source, line range, content, score when relevant, and metadata.
