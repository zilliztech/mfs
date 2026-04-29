# Document Search Artifacts

This folder keeps machine-readable summaries for the document-search
evaluation. The public write-up uses descriptive workflow names; the JSONL
files preserve the original run labels for traceability.

## Workflow Name Mapping

| Artifact value | Public workflow |
| --- | --- |
| `A0` | Agent shell tools |
| `A0S` | Agent shell tools with strategy |
| `A1` | Agent shell tools plus MFS search |
| `A2` | Agent shell tools plus MFS browse |
| `A3` | Agent shell tools plus MFS search + MFS browse |

## Files

```text
artifacts/
  results_summary.jsonl
  retrieval_summary.jsonl
```

`fresh_io_tokens` is the headline token-usage field used by the public tables.
For Codex CLI runs, it is `input_tokens - cached_input_tokens + output_tokens`.
Cached input/cache-read tokens are excluded because they mostly reflect
provider-side cache reuse across repeated non-interactive runs, not fresh
corpus context the agent had to consume.
