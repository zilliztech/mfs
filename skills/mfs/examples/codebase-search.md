# Codebase Search Examples

Use these patterns when the user asks where behavior is implemented, how a
module works, or which file should be edited.

## Find an Implementation from Intent

Task:

```text
Find where stale summaries are tracked.
```

Workflow:

```bash
mfs search "where stale summaries are tracked" . --top-k 10
mfs cat --peek -H 20 -D 3 <candidate-file>
mfs cat -n <start>:<end> <candidate-file>
```

Reasoning:

- Search locates likely implementation chunks.
- `--peek` checks whether the file owns the behavior or only mentions it.
- A line window verifies the exact code before answering or editing.

## Exact Symbol or Error Code

Task:

```text
Find references to ERR_TOKEN_EXPIRED.
```

Workflow:

```bash
grep -R -n "ERR_TOKEN_EXPIRED" .
```

If the corpus has mixed indexed and non-indexed files and MFS routing is useful:

```bash
mfs grep "ERR_TOKEN_EXPIRED" .
```

Reasoning:

Exact tokens are a native search problem first. Use semantic search only if the
literal search does not answer the conceptual question.

## Search Result Is Related but Too Narrow

Task:

```text
Explain how PDF conversion cache eviction works.
```

Workflow:

```bash
mfs search "PDF conversion cache eviction" . --top-k 10
mfs cat --skim -H 12 -D 3 -W 160 <candidate-file>
mfs cat -n <line-start>:<line-end> <candidate-file>
```

Reasoning:

Search may land inside one helper. `--skim` shows nearby functions and flow.
The line window confirms the exact mechanism.

## Search Looks Off-Topic

Task:

```text
Find the handler registry callback registration logic.
```

Workflow:

```bash
mfs search "handler registry callback registration logic" . --top-k 20
mfs search "register callback into hook handler registry" . --top-k 20
mfs cat --peek -H 20 -D 3 <plausible-candidate>
grep -R -n -E "register.*callback|callback.*register|registry" <likely-scope>
```

Reasoning:

Try at least one paraphrase before falling back to regex exploration. When
search results are weak, inspect plausible candidates by structure instead of
reading whole files.
