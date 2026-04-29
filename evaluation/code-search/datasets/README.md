# Code Search Task Manifest

This folder publishes the lightweight task manifest used by the code-search
evaluation. It does not redistribute source-code bodies from CodeSearchNet.
Download the underlying corpus from
[CodeSearchNet](https://github.com/github/CodeSearchNet) under its upstream
terms.

## Files

```text
datasets/
  tasks.jsonl
```

Each JSONL row contains:

- `task_id`: stable task identifier used in the run summaries
- `source_dataset`: upstream data source
- `query`: natural-language request given to the agent
- `difficulty`: easy, medium, or hard query tier
- `expected_path`: target source file path within the evaluation corpus

The manifest contains 24 tasks, with 8 easy, 8 medium, and 8 hard queries.
The ground-truth files were kept in the sampled 2,000-file corpus; extra files
were sampled as distractors with repository diversity controls.
