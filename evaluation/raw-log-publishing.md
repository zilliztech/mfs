# Evaluation Artifact Publishing

The public evaluation pages in this repository use cleaned summaries and small
examples. Full raw logs are better published as external artifacts, for example
in a Hugging Face dataset, after redaction.

## Recommended Shape

```text
mfs-evaluation/
  code-search/
    results_summary.jsonl
    raw_transcripts_sanitized.tar.zst
  document-search/
    results_summary.jsonl
    retrieval_summary.jsonl
    raw_transcripts_sanitized.tar.zst
  README.md
```

## Redaction Checklist

Before publishing raw logs:

- Replace local absolute paths with placeholders such as `<repo>`,
  `<wixqa-corpus>`, and `<codesearchnet-corpus>`.
- Remove session IDs, thread IDs, UUIDs, and local account/profile names.
- Search for real credentials and environment variables:
  `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `ZILLIZ_TOKEN`, `Authorization:`,
  `Bearer`, `sk-`, `ghp_`, and `github_pat_`.
- Keep evaluation prompts and task definitions only if they do not reveal hidden
  answers to the agent at runtime.
- Separate public results from internal iteration notes. Internal notes may
  include language such as regressions, force-stops, aborted runs, or design
  pivots that is useful for engineering but distracting in a public artifact.
- Publish the final selected run and clearly label exploratory or incomplete
  runs if they are included.

## Current Local Raw Log Sizes

The local raw logs are useful for auditability but are too large and too noisy
for the repository:

| Artifact | Size |
| --- | ---: |
| Code raw transcripts, final/scale runs | about 2.5 MB |
| Document raw transcripts, formal WixQA runs | about 134 MB |
| Full document evaluation workspace | about 1.1 GB |
| Full code evaluation workspace | about 277 MB |

The repository should keep the cleaned summaries and curated excerpts. Full raw
logs should be compressed and published separately only after the checklist
above passes.
