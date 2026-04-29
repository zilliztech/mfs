# Evaluation Artifacts

This folder keeps machine-readable summaries from the evaluation runs. The
public write-up uses descriptive workflow names; these JSONL files preserve the
original run fields for traceability.

## Workflow Name Mapping

| Artifact value | Public name |
| --- | --- |
| `A0` | Native shell |
| `A0S` | Native shell with strategy |
| `A1`, `A1 v2` | MFS search |
| `A2` | MFS browse |
| `A3`, `A3 v2` | MFS search + browse |

## Files

```text
artifacts/
  code-search/
    results_summary.jsonl
  document-search/
    results_summary.jsonl
    retrieval_summary.jsonl
```

The human-readable pages are:

- `evaluation/code-search.md`
- `evaluation/document-search.md`
- `evaluation/examples/`

