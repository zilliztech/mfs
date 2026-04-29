# Search and Browse Workflow

MFS works best as an alternating loop:

1. Locate candidates with search.
2. Compare distinct candidate files.
3. Browse the strongest candidates at controlled density.
4. Verify exact lines before answering or editing.

## Unknown Target

Start with a natural-language query:

```bash
mfs search "<what the user is asking for>" <path> --top-k 10
```

If the first result is strong and the snippet answers the question, use it. If
the answer depends on file-level context, inspect the file:

```bash
mfs cat --peek -H 20 -D 3 <candidate-file>
mfs cat --skim -H 12 -D 3 -W 160 <candidate-file>
```

If the search result includes useful line numbers, read a small window:

```bash
mfs cat -n <start>:<end> <candidate-file>
```

## Weak Search Results

If search results look weak:

1. Rewrite the query with synonyms or more domain terms.
2. Increase `--top-k` to compare more distinct files.
3. Use `mfs cat --peek` on the top candidates to compare structure.
4. Use `rg` only if the task has literal anchors or MFS cannot locate a
   plausible semantic candidate.

Do not immediately grep the same vague words. Literal search is not a better
version of semantic search; it is a different tool.

## Known Scope

When the user gives a directory, scope to it:

```bash
mfs search "<query>" ./docs --top-k 10
mfs grep "<literal>" ./src
```

When the user asks across everything already indexed:

```bash
mfs search "<query>" --all --top-k 20
```

Prefer path scopes when available. They reduce noise and help avoid answers
from unrelated projects.

## Orientation

Use tree for a cheap initial map, especially before broad edits or when the
directory itself is unfamiliar:

```bash
mfs tree --peek -L 2 <path>
```

Then search. Do not spend many turns walking directories when a semantic search
can locate the target directly.

## Verification Before Acting

Before final answers, edits, or file/path claims:

- confirm the file path
- inspect nearby lines with `mfs cat -n`
- compare close candidates when titles or snippets overlap
- check whether the prompt asks for multiple files or multiple facts

For edits, use MFS to locate and understand. Use the normal editor or patch
workflow to change files.

## JSON for Automation

Use `--json` when the output will be parsed by another tool:

```bash
mfs search "token refresh" ./src --json
mfs cat --skim ./docs/auth.md --json
```

Human-readable output is usually better for interactive reasoning.
