---
name: mfs
description: Use MFS to find, inspect, and verify information in local indexed files with semantic search, exact grep, and progressive browse commands.
---

# MFS

MFS is a shell-native retrieval layer over local files. Use it when a task
requires finding, inspecting, or verifying information across an indexed
codebase, documentation tree, memory folder, transcript archive, or mixed local
file collection.

MFS has two complementary legs:

- Search locates candidate files and chunks.
- Browse verifies structure and surrounding context before you answer or edit.

Native shell tools still matter. Use `rg`, `find`, and ordinary file reads for
literal strings, filename patterns, and already-known small files.

## First Checks

Before relying on MFS, check that the command exists:

```bash
mfs --help
```

If the target folder may not be indexed, inspect status or ask before running a
large indexing job:

```bash
mfs status
```

Use `mfs add <path>` only when indexing is clearly needed for the task.

## Decision Tree

Pick the smallest useful tool for each sub-task:

- Natural-language concept, behavior, policy, procedure, or vague intent:
  start with `mfs search "<query>" <path>` or `mfs search "<query>" --all`.
- Exact identifier, error code, unique phrase, URL, config key, or import path:
  use `rg` first, or `mfs grep` when you want MFS's indexed/fallback routing.
- Filename or directory pattern:
  use `find`, `fd`, or shell globbing.
- Known file, need outline or section map:
  use `mfs cat --peek <file>`.
- Known file, need compact context:
  use `mfs cat --skim <file>`.
- Search hit is relevant but too narrow:
  use `mfs cat -n <start>:<end> <file>` around the hit's line range.
- Several close candidates:
  compare distinct files using path, title, headings, snippets, and relevant
  line windows before choosing.

`mfs search` and `mfs grep` require an explicit path scope or `--all` unless
they are reading from stdin.

## Recommended Flow

For unknown semantic targets:

```bash
mfs search "<natural-language query>" <path> --top-k 10
```

Then inspect only as much as needed:

```bash
mfs cat --peek -H 20 -D 3 <candidate-file>
mfs cat --skim -H 12 -D 3 -W 160 <candidate-file>
mfs cat -n <start>:<end> <candidate-file>
```

For a new or unfamiliar folder, a cheap map can help:

```bash
mfs tree --peek -L 2 <path>
```

Do not use directory browsing as a substitute for semantic search when the
target is unknown and conceptual. Use it to orient or verify.

## Candidate Selection

Think at the file or article level, not only at the chunk level.

- Merge repeated hits from the same file mentally into one candidate.
- Compare top distinct candidates when titles or snippets are adjacent.
- Prefer a file whose main topic directly matches the request over a broad
  overview that only contains one locally relevant paragraph.
- For multi-part prompts, check whether more than one file is needed.
- If the top result looks off-topic, rewrite the query with synonyms or more
  domain terms before falling back to literal search.

For deeper guidance, read `references/candidate-selection.md`.

## Anti-Patterns

- Do not run grep only to prove that a successful MFS search hit exists; search
  snippets are already file content.
- Do not read a whole large file when `--peek`, `--skim`, or `-n A:B` can
  answer the question.
- Do not blindly choose rank 1 when several related files are returned.
- Do not stop after one correct-looking file if the prompt asks for multiple
  entities, actions, or constraints.
- Do not use MFS for network resources; fetch or clone them locally first.

## References

Read these only when needed:

- `references/command-reference.md` for exact command syntax and options.
- `references/workflow.md` for search-plus-browse usage patterns.
- `references/candidate-selection.md` for choosing between close candidates.
