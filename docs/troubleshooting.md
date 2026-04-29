# Troubleshooting

## OpenAI key is missing

Default embeddings use OpenAI. Set a key or switch providers.

```bash
export OPENAI_API_KEY="sk-..."
```

Or use local ONNX embeddings:

```bash
uv sync --extra onnx
mfs config set embedding.provider onnx
```

## Milvus Lite is locked

Milvus Lite is file-backed and works best with a single writer. If a watcher or
worker is writing, read commands may briefly report that the index is locked.
Wait a moment and retry.

For heavier concurrent use, configure self-hosted Milvus or Zilliz Cloud.

## Search returns no results

Check these first:

```bash
mfs status
mfs search "query" .
mfs search "query" --all
```

Common causes:

- the background worker has not finished yet
- the searched path was not indexed
- the file extension is not embedded by default
- the query is too literal or too vague for the selected mode

Try `--mode hybrid`, then verify with `mfs grep` and `mfs tree --peek`.

## PDF or DOCX conversion fails

The base project includes `pymupdf4llm` and `python-docx`. If your environment
was created before those dependencies were added, refresh it:

```bash
uv sync
```

Then retry:

```bash
mfs cat --skim ./file.pdf
mfs add ./file.docx --sync
```

## `mfs grep` behaves differently from system grep

MFS grep combines indexed search and system grep fallback. It always shows line
numbers in the output gutter. The `-n` option is kept for compatibility and may
emit a warning.

Use system `grep` directly when you need exact GNU grep behavior.
