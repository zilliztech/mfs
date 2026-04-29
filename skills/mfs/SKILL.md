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

## Operating Model

Use MFS as a retrieval workflow, not as a replacement for all shell commands:

1. Locate likely candidates with semantic or hybrid search.
2. Collapse repeated hits from the same file into distinct candidates.
3. Browse the strongest candidates at bounded density.
4. Verify exact lines before answering, citing, or editing.

The goal is to avoid two extremes: reading whole files too early, or trusting a
single search chunk without checking surrounding context.

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

## Command Cheat Sheet

### Search

Use search for natural-language or paraphrased intent:

```bash
mfs search "<query>" <path>
mfs search "<query>" --all
mfs search "<query>" <path> --top-k 20
mfs search "<query>" <path> --mode hybrid
mfs search "<query>" <path> --mode semantic
mfs search "<query>" <path> --mode keyword
```

Guidance:

- `hybrid` is the default and usually the best first choice.
- `semantic` helps when wording may differ from the files.
- `keyword` helps when important literals should influence ranking.
- Prefer a path scope when the user gives one; use `--all` only for all indexed
  content.
- Use `--json` when another tool will parse the output.

### Grep

Use grep for exact text, regex, identifiers, error codes, config keys, and
unique phrases:

```bash
mfs grep "<pattern>" <path>
mfs grep "<pattern>" --all
mfs grep -C 5 "<pattern>" <path>
mfs grep -i "<pattern>" <path>
```

Use native `rg` directly when you need normal ripgrep behavior or the task is
clearly a literal search. Use `mfs grep` when you want MFS's indexed/fallback
routing across mixed files.

### Cat

Use cat for a known file:

```bash
mfs cat <file>
mfs cat --peek <file>
mfs cat --skim <file>
mfs cat --deep <file>
mfs cat -n 40:90 <file>
mfs cat --skim -H 12 -D 3 -W 160 <file>
```

Density ladder:

| Mode | Use |
| --- | --- |
| `--peek` | outline, headings, signatures, file shape |
| `--skim` | compact overview with short excerpts |
| `--deep` | richer structured expansion |
| `-n A:B` | exact line window for final verification |

Budget knobs:

- `-W N`: characters per node or excerpt
- `-H N`: number of top-level items
- `-D N`: structure levels to expand

### Directory Browse

Use directory browse to orient after you know a scope, not as a replacement for
semantic search:

```bash
mfs tree --peek -L 2 <dir>
mfs tree --skim -L 3 <dir>
mfs ls --skim <dir>
```

`tree` and `ls` accept `--peek`, `--skim`, `--deep`, `-W`, `-H`, `-D`, and
`--json`.

### Status and Indexing

Use status to check whether indexing is ready:

```bash
mfs status
mfs status --json
mfs status --needs-summary
```

Use add only when indexing is part of the task:

```bash
mfs add <path>
mfs add <path> --sync
mfs add <path> --force
mfs add <path> --watch
```

Default `mfs add` queues embedding work and starts a background worker.
`--sync` runs embedding in the foreground.

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

## Weak Search Results

If search results look weak or off-topic:

1. Rewrite the query with synonyms, related terms, or more domain context.
2. Increase `--top-k` and compare distinct candidate files.
3. Use `mfs cat --peek` on plausible candidates to compare structure.
4. Use literal search only if the task has exact anchors or MFS cannot locate a
   plausible semantic candidate.

Do not immediately grep the same vague words. Literal search is not a better
version of semantic search; it is a different tool.

## Candidate Selection

Think at the file or article level, not only at the chunk level.

- Merge repeated hits from the same file mentally into one candidate.
- Compare top distinct candidates when titles or snippets are adjacent.
- Prefer a file whose main topic directly matches the request over a broad
  overview that only contains one locally relevant paragraph.
- For multi-part prompts, check whether more than one file is needed.
- If the top result looks off-topic, rewrite the query with synonyms or more
  domain terms before falling back to literal search.

Useful comparison pattern:

```bash
mfs search "<query>" <path> --top-k 20
mfs cat --peek -H 20 -D 3 <candidate-a>
mfs cat --peek -H 20 -D 3 <candidate-b>
mfs cat -n <start>:<end> <best-candidate>
```

Prefer the candidate whose document-level or module-level purpose directly
matches the request. A broad overview that merely contains one matching
paragraph is weaker than a specific task, troubleshooting, API, reference, or
implementation file whose main topic is the requested action.

## Multi-Part Prompts

Check whether the prompt asks for more than one target. Multiple files may be
needed when:

- the prompt mentions two entities, products, modules, or actions
- there is both a setup step and a troubleshooting step
- a migration mentions source and target systems
- a policy/background answer and a procedure are both needed
- search repeatedly points to two complementary candidates

If multiple files are clearly supported, use all required evidence or return all
required paths.

## Code and Document Selection

For code:

- Prefer candidates containing the actual implementation or relevant test.
- Check package/module path, symbol name, class, function, config key, and
  surrounding code.
- If search finds a helper but not the owner module, inspect imports, callers,
  or nearby module structure with native tools and `mfs cat --peek`.

For documents:

- Prefer candidates whose title, path, headings, and snippet directly answer
  the user question.
- Compare adjacent pages such as setup vs troubleshooting, overview vs how-to,
  reference vs implementation, desktop vs mobile, or generic vs product-specific
  docs.
- Inspect at least two outlines when adjacent candidates appear.

## File Types

Indexed by default:

- Markdown, reStructuredText, plain text
- common source code and script extensions
- PDF and DOCX after Markdown conversion

Readable and grep-able, but not embedded by default:

- JSON, JSONL, CSV, TSV
- YAML, TOML, INI, env files
- HTML, XML, CSS, logs

Images can be searched only when text descriptions have been added.

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

Most common guidance is already in this file. Read these references only when
you need more detail or examples:

- `references/command-reference.md` for exact command syntax and options.
- `references/workflow.md` for search-plus-browse usage patterns.
- `references/candidate-selection.md` for choosing between close candidates.
- `examples/codebase-search.md` for implementation-search examples.
- `examples/document-search.md` for documentation-search examples.
- `examples/memory-search.md` for memory, log, and transcript examples.
