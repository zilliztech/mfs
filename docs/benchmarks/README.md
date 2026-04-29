# Benchmarks

MFS is designed for agent retrieval: first find the right place with semantic
search, then inspect just enough surrounding structure with compact browse.
These benchmarks evaluate that workflow in two settings that agents encounter
often:

- code navigation across a Python corpus
- support-document retrieval across a large help-center corpus

The goal is not to show that indexed search replaces native tools. Native
`grep`, `find`, and file reads are still excellent when the query has a literal
anchor. The value of MFS shows up when the agent has to bridge paraphrases,
avoid false-positive keyword hits, and verify candidates without reading full
files.

## Results

| Scenario | Best MFS arm | Main result |
| --- | --- | --- |
| Code search | A3: search + browse | Highest accuracy, lowest pure I/O tokens, fewer turns than native baseline |
| Document search | A3 v2: search + browse | Same hit_all as browse-heavy baseline, with fewer tokens, commands, and wall time |

## Scenarios

- [Code Search](code-search/report.md): CodeSearchNet Python subset, 24
  tasks across easy / medium / hard tiers.
- [Document Search](document-search/report.md): WixQA full corpus, 40
  expert-written help-center questions across single-document and
  multi-document tasks.

## Artifacts

This directory intentionally contains cleaned, lightweight artifacts:

- per-task summary JSONL files
- compact reports
- selected transcript excerpts with local paths redacted

It does not include full corpora, vector indexes, or raw transcript dumps. Those
files are large and contain local paths and full tool traces. If raw logs are
published externally, use the redaction checklist in
[Raw Log Publishing](raw-log-publishing.md).
