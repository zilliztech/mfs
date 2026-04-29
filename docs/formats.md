# File Formats

MFS separates files into three groups: indexed, readable/searchable, and
ignored.

## Indexed by default

| Group | Extensions | Notes |
| --- | --- | --- |
| Markdown | `.md`, `.rst`, `.markdown` | heading-aware chunking |
| Text | `.txt` | plain text chunking |
| Code | `.py`, `.js`, `.ts`, `.tsx`, `.go`, `.rs`, `.java`, `.sh`, `.sql`, `.tf`, and more | code-oriented chunking and metadata |
| Documents | `.pdf`, `.docx` | converted to Markdown before chunking |

PDF conversion uses `pymupdf4llm`. DOCX conversion uses `python-docx`.

Converted Markdown is cached under:

```text
~/.mfs/converted/
```

The cache is bounded by:

```toml
[cache]
max_size_mb = 500
```

MFS evicts least-recently-used converted files when the cache exceeds the cap.

## Readable and grep-able, not embedded by default

| Group | Extensions |
| --- | --- |
| Structured data | `.json`, `.jsonl`, `.ndjson`, `.csv`, `.tsv` |
| Config | `.yaml`, `.yml`, `.toml`, `.ini`, `.env` |
| Web/style | `.html`, `.htm`, `.xml`, `.css`, `.scss`, `.sass`, `.less` |
| Logs | `.log` |

These files are useful to inspect, but often noisy for semantic embedding.
MFS keeps them available through:

```bash
mfs grep "literal token" ./data
mfs cat --skim ./data/events.jsonl
mfs cat --peek ./data/config.json
```

## Images

Image files are not directly embedded. MFS can index an image description as
text if you provide one or opt into VLM description generation.

```bash
mfs add ./assets/diagram.png --description "Architecture diagram ..."
mfs add ./assets --describe
```

Supported image extensions for description workflows include PNG, JPG/JPEG,
GIF, WEBP, and BMP.

## Ignored by default

Common binary, build, lock, media, archive, and virtual environment paths are
ignored. Examples include:

- `.git`, `node_modules`, `.venv`, `dist`, `build`
- `.pyc`, `.class`, `.so`, `.dll`, `.exe`
- `.zip`, `.tar`, `.gz`, `.7z`
- `package-lock.json`

Use `.gitignore`, `.mfsignore`, config include/exclude lists, or `mfs add
--exclude` to tune indexing.
