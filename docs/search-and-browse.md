# Search and Browse

MFS is most useful when search and browse are used together.

## Search

Use search when the answer could be anywhere in the indexed corpus.

```bash
mfs search "how is the queue drained" .
mfs search "structured jsonl display" ./src --mode hybrid
mfs search "OPENAI_API_KEY" . --mode keyword
```

Modes:

| Mode | Use it when |
| --- | --- |
| `hybrid` | default; best general-purpose mode |
| `semantic` | wording may differ from the documents |
| `keyword` | exact identifiers, error codes, config keys |

## Grep

Use grep when the literal token matters.

```bash
mfs grep "queue.json" .
mfs grep -C 3 "cache.max_size_mb" .
mfs grep -i "zilliz" --all
```

## Browse

Use browse when you need orientation or verification.

```bash
mfs tree --peek -L 2 .
mfs ls --skim ./docs
mfs cat --skim ./docs/architecture.md
mfs cat -n 120:180 ./src/mfs/cli.py
```

Density presets:

| Preset | Output shape | Typical use |
| --- | --- | --- |
| `--peek` | skeleton, headings, filenames | orient quickly |
| `--skim` | compact summaries or excerpts | decide where to inspect |
| `--deep` | richer expansion | prepare for edits or final verification |

The knobs behind the presets:

| Option | Meaning |
| --- | --- |
| `-W` | width: characters per node or excerpt |
| `-H` | height: number of top-level items |
| `-D` | depth: structure levels to expand |

## A good agent loop

```bash
# 1. Get the map.
mfs tree --peek -L 2 .

# 2. Search globally inside the current project.
mfs search "how are pdf files converted and cached" . --top-k 5

# 3. Inspect the candidate file in bounded form.
mfs cat --skim ./src/mfs/ingest/converter.py

# 4. Read the exact lines before changing anything.
mfs cat -n 1:140 ./src/mfs/ingest/converter.py
```

Search should locate. Browse should verify. Line ranges should be used before
editing or quoting behavior.
