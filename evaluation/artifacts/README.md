# Evaluation Artifacts

This folder keeps machine-readable summaries from the evaluation runs. The
public write-up uses descriptive workflow names; these JSONL files preserve the
original run fields for traceability.

## Workflow Name Mapping

| Artifact value | Public name |
| --- | --- |
| `A0` | Agent shell tools |
| `A0S` | Agent shell tools with strategy |
| `A1`, `A1 v2` | MFS search |
| `A2` | MFS browse |
| `A3`, `A3 v2` | MFS search + MFS browse |

## Files

```text
artifacts/
  code-search/
    results_summary.jsonl
  document-search/
    results_summary.jsonl
    retrieval_summary.jsonl
  experiment-skills/
    README.md
    code-search/
    document-search/
```

The human-readable pages are:

- `evaluation/code-search.md`
- `evaluation/document-search.md`
- `evaluation/examples/`

`fresh_io_tokens` is the headline cost field used by the public tables. For
Codex CLI runs it excludes cached input tokens and includes output tokens; for
Claude Code runs it matches the stored `pure_io_tokens` field. Earlier
document-search `A1` and `A3` rows are retained for traceability, while the
public result tables use the later `A1 v2` and `A3 v2` rows.
