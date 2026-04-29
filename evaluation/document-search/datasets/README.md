# Document Search Task Manifest

This folder publishes the lightweight task manifest used by the document-search
evaluation. It does not redistribute WixQA article bodies. Download the
underlying corpus from [WixQA](https://huggingface.co/datasets/Wix/WixQA)
under its upstream terms.

## Files

```text
datasets/
  tasks.jsonl
```

Each JSONL row contains:

- `task_id`: stable task identifier used in the run summaries
- `source_dataset`: upstream data source
- `question`: support-style question given to the agent
- `expected_paths`: one or more target article paths within the evaluation
  corpus

The manifest contains 40 tasks selected from the WixQA expert-written test
split with fixed seed `20260428`: 30 single-article tasks and 10 multi-article
tasks. Selection required clean ground-truth article mappings and did not use
MFS retrieval or shell-search results.
