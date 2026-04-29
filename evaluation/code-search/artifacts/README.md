# Code Search Artifacts

This folder keeps the machine-readable summary for the code-search evaluation.
The public write-up uses descriptive workflow names; the JSONL file preserves
the original run labels for traceability.

## Workflow Name Mapping

| Artifact value | Public workflow |
| --- | --- |
| `A0` | Agent shell tools |
| `A1` | Agent shell tools plus MFS search |
| `A2` | Agent shell tools plus MFS browse |
| `A3` | Agent shell tools plus MFS search + MFS browse |

## Files

```text
artifacts/
  results_summary.jsonl
```

`fresh_io_tokens` is the headline token-usage field used by the public tables.
For this Claude Code run, it matches `input_tokens + output_tokens`. The raw
summary also keeps secondary token fields such as `linear_tokens` and
`with_read_tokens`.
