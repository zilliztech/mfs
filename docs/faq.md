# FAQ

## Is MFS a filesystem?

No. MFS does not mount, move, sync, or store your files. It builds a search
index over files that already exist on disk.

## Does MFS modify my project?

No. Runtime state lives under `~/.mfs/` by default. Your project directory is
not given generated metadata files.

## Why does `mfs search` require a path or `--all`?

Explicit scope prevents surprising searches against the wrong corpus. Use:

```bash
mfs search "query" .
mfs search "query" --all
```

## What happens if background indexing is interrupted?

The queue is lightweight local state. Re-run `mfs add .` to continue, or use
`mfs add . --force` when you want a stronger rebuild pass.

## Are PDF and DOCX files supported?

Yes. PDF uses `pymupdf4llm`; DOCX uses `python-docx`. Both are converted to
Markdown before chunking and cached in `~/.mfs/converted/`.

## Are JSON, JSONL, and CSV indexed semantically?

Not by default. They are readable with `mfs cat` and searchable with `mfs grep`.
This avoids embedding noisy structured data unless a future workflow chooses to
index it explicitly.

## Does MFS call an LLM while indexing?

Not by default. Embedding is required; LLM summaries and VLM descriptions are
opt-in.

## Can I use Zilliz Cloud?

Yes. Set `milvus.uri` to your Zilliz Cloud endpoint and `milvus.token` to the
token.
